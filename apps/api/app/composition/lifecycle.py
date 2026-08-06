import asyncio
from contextlib import AsyncExitStack


class RuntimeCleanup:
    def __init__(self, stack: AsyncExitStack) -> None:
        self._stack = stack
        self._lock = asyncio.Lock()
        self._close_task: asyncio.Task[None] | None = None

    async def aclose(self) -> None:
        async with self._lock:
            if self._close_task is None:
                self._close_task = asyncio.create_task(self._stack.aclose())
            close_task = self._close_task
        await asyncio.shield(close_task)
