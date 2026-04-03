import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.auth import ClerkAuthProvider
from app.core.config import get_settings
from app.core.logging import setup_logging
from app.core.redis import RedisEventBus, create_arq_pool
from app.routers.agent import router as agent_router
from app.routers.billing import router as billing_router
from app.routers.calls import router as calls_router
from app.routers.health import router as health_router
from app.routers.websocket import router as websocket_router
from app.services.realtime_service import RealtimeService
from app.websockets.manager import manager as websocket_manager
from app.workers.call_finalization_queue import CallFinalizationQueue
from app.webhooks.clerk import router as clerk_webhook_router
from app.webhooks.livekit import router as livekit_webhook_router
from app.webhooks.stripe import router as stripe_webhook_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    get_settings.cache_clear()
    settings = get_settings()
    app.state.settings = settings
    app.state.realtime_service = RealtimeService(
        auth_provider=ClerkAuthProvider(),
        event_bus=RedisEventBus(),
        websocket_manager=websocket_manager,
    )
    app.state.call_finalization_queue = None
    app.state.livekit_api = None
    app.state.livekit_webhook_receiver = None
    relay_task = None
    call_finalization_pool = None
    if settings.app_env != "test":
        call_finalization_pool = await create_arq_pool()
        app.state.arq_pool = call_finalization_pool
        app.state.call_finalization_queue = CallFinalizationQueue(call_finalization_pool)
        relay_task = asyncio.create_task(app.state.realtime_service.fanout_forever())
        if settings.livekit_url and settings.livekit_api_key and settings.livekit_api_secret:
            from livekit import api as livekit_api_module

            app.state.livekit_api = livekit_api_module.LiveKitAPI(
                url=settings.livekit_url,
                api_key=settings.livekit_api_key,
                api_secret=settings.livekit_api_secret,
            )
            verifier = livekit_api_module.TokenVerifier(settings.livekit_api_key, settings.livekit_api_secret)
            app.state.livekit_webhook_receiver = livekit_api_module.WebhookReceiver(verifier)

    try:
        yield
    finally:
        if relay_task is not None:
            relay_task.cancel()
            try:
                await relay_task
            except asyncio.CancelledError:
                pass
        if app.state.livekit_api is not None:
            await app.state.livekit_api.aclose()
        if call_finalization_pool is not None:
            close = getattr(call_finalization_pool, "aclose", None)
            if close is not None:
                await close()
            else:
                await call_finalization_pool.close()


app = FastAPI(lifespan=lifespan)

settings = get_settings()
if settings.cors_allowed_origins:
    origins = [origin.strip() for origin in settings.cors_allowed_origins.split(",") if origin.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

app.include_router(agent_router)
app.include_router(billing_router)
app.include_router(calls_router)
app.include_router(health_router)
app.include_router(websocket_router)
app.include_router(clerk_webhook_router)
app.include_router(livekit_webhook_router)
app.include_router(stripe_webhook_router)
