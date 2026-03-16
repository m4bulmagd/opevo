import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.config import get_settings
from app.core.logging import setup_logging
from app.core.redis import create_arq_pool
from app.routers.agent import router as agent_router
from app.routers.billing import router as billing_router
from app.routers.calls import router as calls_router
from app.routers.health import router as health_router
from app.routers.websocket import router as websocket_router
from app.services.realtime_service import RealtimeService
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
    app.state.realtime_service = RealtimeService()
    app.state.call_finalization_queue = None
    relay_task = None
    call_finalization_pool = None
    if settings.app_env != "test":
        call_finalization_pool = await create_arq_pool()
        app.state.call_finalization_queue = CallFinalizationQueue(call_finalization_pool)
        relay_task = asyncio.create_task(app.state.realtime_service.fanout_forever())

    try:
        yield
    finally:
        if relay_task is not None:
            relay_task.cancel()
            try:
                await relay_task
            except asyncio.CancelledError:
                pass
        if call_finalization_pool is not None:
            close = getattr(call_finalization_pool, "aclose", None)
            if close is not None:
                await close()
            else:
                await call_finalization_pool.close()


app = FastAPI(lifespan=lifespan)
app.include_router(agent_router)
app.include_router(billing_router)
app.include_router(calls_router)
app.include_router(health_router)
app.include_router(websocket_router)
app.include_router(clerk_webhook_router)
app.include_router(livekit_webhook_router)
app.include_router(stripe_webhook_router)
