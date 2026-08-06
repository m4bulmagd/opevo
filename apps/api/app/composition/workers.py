from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from contextlib import AsyncExitStack
from datetime import UTC, datetime
import logging

from arq.connections import ArqRedis
from sqlalchemy.ext.asyncio import AsyncEngine

from app.composition.lifecycle import RuntimeCleanup
from app.composition.runtime import (
    BackgroundWorkerRuntime,
    CallLifecycleWorkerRuntime,
)
from app.core.config import Settings
from app.core.database import (
    AsyncSessionFactory,
    create_database_engine,
    create_session_factory,
)
from app.core.logging import report_safe_exception
from app.core.observability import (
    Observability,
    initialize_observability,
    shutdown_observability,
)
from app.core.runtime_validation import (
    validate_background_worker_runtime,
    validate_call_lifecycle_worker_runtime,
)
from app.workers.outbox.delivery import OutboxHandler, get_default_outbox_handlers
from app.workers.queue_observer import QueueObserver
from app.workers.queueing import (
    BACKGROUND_QUEUE_NAME,
    CALL_LIFECYCLE_QUEUE_NAME,
    QUEUE_CLASS_BACKGROUND,
    QUEUE_CLASS_CALL_LIFECYCLE,
)


logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(UTC)


async def _close_observability(observability: Observability) -> None:
    close = getattr(observability, "aclose", None)
    if callable(close):
        await close()
        return
    await shutdown_observability(observability)


async def _dispose_engine(engine: AsyncEngine) -> None:
    await engine.dispose()


async def _wait_for_cleanup(stack: AsyncExitStack) -> BaseException | None:
    cleanup_completed = asyncio.get_running_loop().create_future()
    cleanup_task = asyncio.create_task(stack.aclose())

    def mark_cleanup_complete(_task: asyncio.Task[None]) -> None:
        cleanup_completed.set_result(None)

    cleanup_task.add_done_callback(mark_cleanup_complete)
    outer_cancellation: asyncio.CancelledError | None = None
    while not cleanup_completed.done():
        try:
            await asyncio.shield(cleanup_completed)
        except asyncio.CancelledError as error:
            if outer_cancellation is None:
                outer_cancellation = error

    cleanup_failure: BaseException | None = None
    try:
        cleanup_task.result()
    except BaseException as error:
        cleanup_failure = error
    return outer_cancellation or cleanup_failure


async def _unwind_partial_runtime(
    stack: AsyncExitStack,
    construction_error: BaseException,
) -> None:
    cleanup_failure = await _wait_for_cleanup(stack)
    construction_cancellation = (
        construction_error
        if isinstance(construction_error, asyncio.CancelledError)
        else None
    )
    cleanup_cancellation = (
        cleanup_failure
        if isinstance(cleanup_failure, asyncio.CancelledError)
        else None
    )
    cancellation = construction_cancellation or cleanup_cancellation
    if cancellation is not None:
        cancellation.__traceback__ = None
        raise cancellation from None
    if cleanup_failure is not None:
        report_safe_exception(
            logger,
            event="worker_runtime_partial_cleanup_failed",
            operation="close_partial_worker_runtime",
            error=cleanup_failure,
            status="failed",
            level=logging.WARNING,
        )


async def build_call_lifecycle_worker_runtime(
    settings: Settings,
    *,
    arq_redis: ArqRedis,
    engine_factory: Callable[[str], AsyncEngine] = create_database_engine,
    session_factory_factory: Callable[
        [AsyncEngine], AsyncSessionFactory
    ] = create_session_factory,
    observability_factory: Callable[..., Observability] = initialize_observability,
    observer_factory: Callable[..., QueueObserver] = QueueObserver,
    now: Callable[[], datetime] = _utc_now,
) -> CallLifecycleWorkerRuntime:
    validate_call_lifecycle_worker_runtime(settings)
    stack = AsyncExitStack()
    await stack.__aenter__()
    cleanup = RuntimeCleanup(stack)
    try:
        observability = observability_factory(
            service_name="presvo-worker-call-lifecycle",
            endpoint=settings.otel_exporter_otlp_endpoint,
        )
        stack.push_async_callback(_close_observability, observability)

        engine = engine_factory(settings.database_url)
        stack.push_async_callback(_dispose_engine, engine)
        session_factory = session_factory_factory(engine)

        queue_observer = observer_factory(
            arq_redis,
            observability,
            queue_name=CALL_LIFECYCLE_QUEUE_NAME,
            queue_class=QUEUE_CLASS_CALL_LIFECYCLE,
        )
        stack.push_async_callback(queue_observer.aclose)
        queue_observer.start()

        return CallLifecycleWorkerRuntime(
            settings=settings,
            session_factory=session_factory,
            arq_pool=arq_redis,
            observability=observability,
            queue_observer=queue_observer,
            now=now,
            _cleanup=cleanup,
        )
    except BaseException as construction_error:
        await _unwind_partial_runtime(stack, construction_error)
        raise


async def build_background_worker_runtime(
    settings: Settings,
    *,
    arq_redis: ArqRedis,
    engine_factory: Callable[[str], AsyncEngine] = create_database_engine,
    session_factory_factory: Callable[
        [AsyncEngine], AsyncSessionFactory
    ] = create_session_factory,
    observability_factory: Callable[..., Observability] = initialize_observability,
    observer_factory: Callable[..., QueueObserver] = QueueObserver,
    outbox_handlers_factory: Callable[
        [], Mapping[str, OutboxHandler]
    ] = get_default_outbox_handlers,
    now: Callable[[], datetime] = _utc_now,
) -> BackgroundWorkerRuntime:
    validate_background_worker_runtime(settings)
    stack = AsyncExitStack()
    await stack.__aenter__()
    cleanup = RuntimeCleanup(stack)
    try:
        observability = observability_factory(
            service_name="presvo-worker-background",
            endpoint=settings.otel_exporter_otlp_endpoint,
        )
        stack.push_async_callback(_close_observability, observability)

        engine = engine_factory(settings.database_url)
        stack.push_async_callback(_dispose_engine, engine)
        session_factory = session_factory_factory(engine)
        outbox_handlers = outbox_handlers_factory()

        queue_observer = observer_factory(
            arq_redis,
            observability,
            queue_name=BACKGROUND_QUEUE_NAME,
            queue_class=QUEUE_CLASS_BACKGROUND,
        )
        stack.push_async_callback(queue_observer.aclose)
        queue_observer.start()

        return BackgroundWorkerRuntime(
            settings=settings,
            session_factory=session_factory,
            arq_pool=arq_redis,
            observability=observability,
            queue_observer=queue_observer,
            outbox_handlers=outbox_handlers,
            now=now,
            _cleanup=cleanup,
        )
    except BaseException as construction_error:
        await _unwind_partial_runtime(stack, construction_error)
        raise
