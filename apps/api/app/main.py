import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.core.auth import ClerkAuthProvider
from app.core.config import Settings, get_settings
from app.core.logging import report_safe_exception, setup_logging
from app.core.observability import (
    initialize_observability,
    install_http_observability,
    shutdown_observability,
)
from app.core.rate_limit import configure_rate_limiter
from app.core.redis import RedisEventBus, create_arq_pool
from app.core.runtime_validation import validate_api_runtime
from app.routers.activation import router as activation_router
from app.routers.account import router as account_router
from app.routers.agent import router as agent_router
from app.routers.billing import router as billing_router
from app.routers.calls import router as calls_router
from app.routers.development import router as development_router
from app.routers.health import router as health_router
from app.routers.onboarding import router as onboarding_router
from app.routers.readiness import (
    create_readiness_checks,
    router as readiness_router,
)
from app.routers.websocket import router as websocket_router
from app.services.realtime_service import RealtimeService
from app.websockets.manager import manager as websocket_manager
from app.workers.call_finalization_queue import CallFinalizationQueue
from app.webhooks.clerk import router as clerk_webhook_router
from app.webhooks.livekit import router as livekit_webhook_router
from app.webhooks.stripe import router as stripe_webhook_router


logger = logging.getLogger(__name__)


def _handle_rate_limit_exception(request: Request, exc: Exception) -> Response:
    if not isinstance(exc, RateLimitExceeded):
        raise exc
    return _rate_limit_exceeded_handler(request, exc)


async def _stop_realtime_fanout(relay_task: asyncio.Task | None) -> None:
    if relay_task is None:
        return
    if not relay_task.done():
        relay_task.cancel()
    try:
        await relay_task
    except asyncio.CancelledError:
        pass
    except Exception as error:
        report_safe_exception(
            logger,
            event="realtime_fanout_failed",
            operation="stop_realtime_fanout",
            error=error,
            status="failed",
            level=logging.WARNING,
        )


async def _close_runtime_resource(
    resource,
    *,
    event: str,
    operation: str,
) -> None:
    if resource is None:
        return
    try:
        close = getattr(resource, "aclose", None)
        if close is not None:
            await close()
        else:
            await resource.close()
    except Exception as error:
        report_safe_exception(
            logger,
            event=event,
            operation=operation,
            error=error,
            status="failed",
            level=logging.WARNING,
        )


def _lifespan(settings: Settings):
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        setup_logging()
        app.state.settings = settings
        app.state.realtime_service = None
        app.state.call_finalization_queue = None
        app.state.livekit_webhook_receiver = None
        app.state.arq_pool = None
        app.state.observability = initialize_observability(
            service_name=settings.otel_service_name,
            endpoint=settings.otel_exporter_otlp_endpoint,
        )
        app.state.readiness_checks = None
        relay_task = None
        call_finalization_pool = None
        realtime_event_bus = None
        try:
            app.state.readiness_checks = await create_readiness_checks(settings)
            if settings.realtime_enabled:
                realtime_event_bus = RedisEventBus(redis_url=settings.redis_url)
                app.state.realtime_service = RealtimeService(
                    auth_provider=ClerkAuthProvider(settings=settings),
                    event_bus=realtime_event_bus,
                    websocket_manager=websocket_manager,
                )
            if settings.app_env != "test":
                call_finalization_pool = await create_arq_pool(settings.redis_url)
                app.state.arq_pool = call_finalization_pool
                app.state.call_finalization_queue = CallFinalizationQueue(
                    call_finalization_pool
                )
                if app.state.realtime_service is not None:
                    relay_task = asyncio.create_task(
                        app.state.realtime_service.fanout_forever()
                    )
                if (
                    settings.livekit_url
                    and settings.livekit_api_key
                    and settings.livekit_api_secret
                ):
                    from livekit import api as livekit_api_module

                    verifier = livekit_api_module.TokenVerifier(
                        settings.livekit_api_key,
                        settings.livekit_api_secret,
                    )
                    app.state.livekit_webhook_receiver = (
                        livekit_api_module.WebhookReceiver(verifier)
                    )
            yield
        finally:
            await _stop_realtime_fanout(relay_task)
            await _close_runtime_resource(
                app.state.readiness_checks,
                event="readiness_checks_close_failed",
                operation="close_readiness_checks",
            )
            await _close_runtime_resource(
                realtime_event_bus,
                event="realtime_bus_close_failed",
                operation="close_realtime_bus",
            )
            await _close_runtime_resource(
                call_finalization_pool,
                event="arq_pool_close_failed",
                operation="close_arq_pool",
            )
            await shutdown_observability(app.state.observability)

    return lifespan


def create_app(settings: Settings | None = None) -> FastAPI:
    configured_settings = settings or get_settings()
    validate_api_runtime(configured_settings)

    application = FastAPI(lifespan=_lifespan(configured_settings))
    application.state.settings = configured_settings
    install_http_observability(application)
    application.state.limiter = configure_rate_limiter(configured_settings)
    application.add_exception_handler(
        RateLimitExceeded,
        _handle_rate_limit_exception,
    )

    if configured_settings.cors_allowed_origins:
        origins = [
            origin.strip()
            for origin in configured_settings.cors_allowed_origins.split(",")
            if origin.strip()
        ]
        application.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    application.include_router(account_router)
    application.include_router(activation_router)
    application.include_router(agent_router)
    application.include_router(billing_router)
    application.include_router(calls_router)
    if configured_settings.app_env == "development":
        application.include_router(development_router)
    application.include_router(health_router)
    application.include_router(onboarding_router)
    application.include_router(readiness_router)
    if configured_settings.realtime_enabled:
        application.include_router(websocket_router)
    application.include_router(clerk_webhook_router)
    application.include_router(livekit_webhook_router)
    application.include_router(stripe_webhook_router)
    return application


app = create_app()
