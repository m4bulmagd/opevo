from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.composition.runtime import get_api_runtime
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


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with get_api_runtime(request.app).session_factory() as session:
        yield session
