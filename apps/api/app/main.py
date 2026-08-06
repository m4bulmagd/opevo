from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.composition.api import ApiRuntimeBuilder, build_api_runtime
from app.core.config import Settings, get_settings
from app.core.observability import install_http_observability
from app.core.rate_limit import configure_rate_limiter
from app.routers.activation import router as activation_router
from app.routers.account import router as account_router
from app.routers.agent import router as agent_router
from app.routers.billing import router as billing_router
from app.routers.calls import router as calls_router
from app.routers.dashboard import router as dashboard_router
from app.routers.development import router as development_router
from app.routers.health import router as health_router
from app.routers.onboarding import router as onboarding_router
from app.routers.readiness import router as readiness_router
from app.routers.websocket import router as websocket_router
from app.webhooks.clerk import router as clerk_webhook_router
from app.webhooks.livekit import router as livekit_webhook_router
from app.webhooks.stripe import router as stripe_webhook_router


def _handle_rate_limit_exception(request: Request, exc: Exception) -> Response:
    if not isinstance(exc, RateLimitExceeded):
        raise exc
    return _rate_limit_exceeded_handler(request, exc)


def _lifespan(settings: Settings, runtime_builder: ApiRuntimeBuilder):
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        runtime = await runtime_builder(settings)
        app.state.runtime = runtime
        try:
            yield
        finally:
            app.state.runtime = None
            await runtime.aclose()

    return lifespan


def create_app(
    settings: Settings | None = None,
    *,
    runtime_builder: ApiRuntimeBuilder = build_api_runtime,
) -> FastAPI:
    configured_settings = settings or get_settings()

    application = FastAPI(
        lifespan=_lifespan(configured_settings, runtime_builder)
    )
    application.state.runtime = None
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
    application.include_router(dashboard_router)
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
