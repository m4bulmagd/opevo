import asyncio
from contextlib import AsyncExitStack

import pytest

from app.composition.lifecycle import RuntimeCleanup


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


async def _record(calls: list[str], value: str) -> None:
    calls.append(value)
