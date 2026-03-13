from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.config import get_settings
from app.core.logging import setup_logging
from app.routers.auth import router as auth_router
from app.webhooks.clerk import router as clerk_webhook_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    app.state.settings = get_settings()
    yield


app = FastAPI(lifespan=lifespan)
app.include_router(auth_router)
app.include_router(clerk_webhook_router)


@app.get("/healthz")
async def healthcheck() -> dict[str, str]:
    return {"status": "ok"}
