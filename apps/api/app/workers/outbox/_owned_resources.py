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

    body_error: BaseException | None = None
    try:
        yield own
    except BaseException as error:
        body_error = error

    cleanup_task = asyncio.create_task(_close_all(resources, operation=operation))
    cancelled_during_cleanup = False
    while not cleanup_task.done():
        try:
            await asyncio.shield(cleanup_task)
        except asyncio.CancelledError:
            cancelled_during_cleanup = True
        except Exception:
            pass

    cleanup_error: Exception | None = None
    cleanup_cancelled = False
    try:
        cleanup_task.result()
    except asyncio.CancelledError:
        cleanup_cancelled = True
    except Exception as error:
        cleanup_error = error

    if (
        isinstance(body_error, asyncio.CancelledError)
        or cancelled_during_cleanup
        or cleanup_cancelled
    ):
        raise asyncio.CancelledError
    if body_error is not None:
        raise body_error
    if cleanup_error is not None:
        raise cleanup_error


async def _close_all(
    resources: list[object],
    *,
    operation: str,
) -> None:
    first_error: Exception | None = None
    cancellation: asyncio.CancelledError | None = None
    for resource in reversed(resources):
        try:
            await _close(resource)
        except asyncio.CancelledError as error:
            if cancellation is None:
                cancellation = error
        except Exception as error:
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
    if cancellation is not None:
        raise cancellation
    if first_error is not None:
        raise first_error


async def _close(resource: object) -> None:
    close = getattr(resource, "aclose", None)
    if callable(close):
        await close()
        return
    close = getattr(resource, "close", None)
    if callable(close):
        await asyncio.to_thread(close)
