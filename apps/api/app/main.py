from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.config import get_settings
from app.core.logging import setup_logging


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    app.state.settings = get_settings()
    yield


app = FastAPI(lifespan=lifespan)


@app.get("/healthz")
async def healthcheck() -> dict[str, str]:
    return {"status": "ok"}
