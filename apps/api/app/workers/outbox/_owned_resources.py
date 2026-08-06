from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator, Callable

from app.core.logging import report_safe_exception


logger = logging.getLogger(__name__)


@asynccontextmanager
async def operation_owned_resources(
    *,
    operation: str,
) -> AsyncIterator[Callable[[object], object]]:
    resources: list[object] = []

    def own(resource: object) -> object:
        resources.append(resource)
        return resource

    try:
        yield own
    except BaseException:
        await _close_all(resources, operation=operation, suppress=True)
        raise
    else:
        await _close_all(resources, operation=operation, suppress=False)


async def _close_all(
    resources: list[object],
    *,
    operation: str,
    suppress: bool,
) -> None:
    first_error: BaseException | None = None
    for resource in reversed(resources):
        try:
            await _close(resource)
        except BaseException as error:
            report_safe_exception(
                logger,
                event="operation_resource_close_failed",
                operation=operation,
                error=error,
                status="failed",
                level=logging.WARNING,
            )
            if first_error is None:
                first_error = error
    if first_error is not None and not suppress:
        raise first_error


async def _close(resource: object) -> None:
    close = getattr(resource, "aclose", None)
    if callable(close):
        await close()
        return
    close = getattr(resource, "close", None)
    if callable(close):
        await asyncio.to_thread(close)
