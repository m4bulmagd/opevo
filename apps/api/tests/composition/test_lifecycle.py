import asyncio
from contextlib import AsyncExitStack

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.pool import AsyncAdaptedQueuePool, StaticPool

from app.composition.lifecycle import RuntimeCleanup
from app.core.database import create_database_engine, create_session_factory


@pytest.mark.anyio
async def test_runtime_cleanup_closes_callbacks_once_in_reverse_order() -> None:
    calls: list[str] = []
    stack = AsyncExitStack()
    stack.push_async_callback(lambda: _record(calls, "first"))
    stack.push_async_callback(lambda: _record(calls, "second"))
    cleanup = RuntimeCleanup(stack)

    await asyncio.gather(cleanup.aclose(), cleanup.aclose())
    await cleanup.aclose()

    assert calls == ["second", "first"]


@pytest.mark.anyio
async def test_runtime_cleanup_continues_after_waiter_cancellation() -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    closed = asyncio.Event()

    async def close_resource() -> None:
        started.set()
        await release.wait()
        closed.set()

    stack = AsyncExitStack()
    stack.push_async_callback(close_resource)
    cleanup = RuntimeCleanup(stack)
    waiter = asyncio.create_task(cleanup.aclose())
    await started.wait()
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    release.set()
    await cleanup.aclose()

    assert closed.is_set()


@pytest.mark.anyio
async def test_create_database_engine_supports_sqlite_without_reading_settings(
) -> None:
    engine = create_database_engine("sqlite+aiosqlite:///:memory:")

    try:
        assert isinstance(engine.pool, StaticPool)
        assert engine.pool._recycle == 1800
        assert engine.pool._pre_ping is True
    finally:
        await engine.dispose()


@pytest.mark.anyio
async def test_create_database_engine_configures_non_sqlite_pool_without_settings(
) -> None:
    engine = create_database_engine(
        "postgresql+asyncpg://postgres:postgres@localhost/runtime_test"
    )

    try:
        assert isinstance(engine.pool, AsyncAdaptedQueuePool)
        assert engine.pool.size() == 10
        assert engine.pool._max_overflow == 20
        assert engine.pool._recycle == 1800
        assert engine.pool._pre_ping is True
    finally:
        await engine.dispose()


@pytest.mark.anyio
async def test_create_session_factory_binds_non_expiring_async_sessions() -> None:
    engine = create_database_engine("sqlite+aiosqlite:///:memory:")
    session_factory = create_session_factory(engine)

    try:
        async with session_factory() as session:
            assert isinstance(session, AsyncSession)
            assert session.bind is engine
            assert session.sync_session.expire_on_commit is False
    finally:
        await engine.dispose()


async def _record(calls: list[str], value: str) -> None:
    calls.append(value)
