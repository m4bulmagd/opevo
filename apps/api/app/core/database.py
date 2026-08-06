from functools import lru_cache
from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.composition.runtime import get_api_runtime
from app.core.config import get_settings


AsyncSessionFactory = async_sessionmaker[AsyncSession]


def create_database_engine(database_url: str) -> AsyncEngine:
    if database_url.startswith("sqlite"):
        return create_async_engine(
            database_url,
            future=True,
            pool_recycle=1800,
            pool_pre_ping=True,
        )

    return create_async_engine(
        database_url,
        future=True,
        pool_size=10,
        max_overflow=20,
        pool_recycle=1800,
        pool_pre_ping=True,
    )


def create_session_factory(engine: AsyncEngine) -> AsyncSessionFactory:
    return async_sessionmaker(engine, expire_on_commit=False)


@lru_cache
def get_engine() -> AsyncEngine:
    settings = get_settings()
    return create_database_engine(settings.database_url)


@lru_cache
def get_session_factory() -> AsyncSessionFactory:
    return create_session_factory(get_engine())


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with get_api_runtime(request.app).session_factory() as session:
        yield session
