from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from app.composition.lifecycle import RuntimeCleanup
from app.core.config import Settings


if TYPE_CHECKING:
    from arq.connections import ArqRedis
    from redis.asyncio import Redis
    from sqlalchemy.ext.asyncio import AsyncEngine

    from app.auth.providers.base import AuthProvider
    from app.core.database import AsyncSessionFactory
    from app.core.observability import Observability
    from app.providers.storage.base import StorageProvider
    from app.routers.readiness import ReadinessChecks
    from app.services.livekit_recording_service import LiveKitRecordingService
    from app.services.realtime_service import RealtimeService
    from app.workers.call_finalization_queue import CallFinalizationQueue
    from app.workers.outbox.registry import OutboxHandler
    from app.workers.queue_observer import QueueObserver


WORKER_RUNTIME_KEY = "application_runtime"


@dataclass(slots=True)
class ApiRuntime:
    settings: Settings
    engine: AsyncEngine
    session_factory: AsyncSessionFactory
    redis_client: Redis
    observability: Observability
    auth_provider: AuthProvider
    readiness_checks: ReadinessChecks
    storage_provider: StorageProvider
    arq_pool: ArqRedis | None
    call_finalization_queue: CallFinalizationQueue | None
    realtime_service: RealtimeService | None
    livekit_webhook_receiver: object | None
    livekit_recording_service: LiveKitRecordingService | None
    _cleanup: RuntimeCleanup

    async def aclose(self) -> None:
        await self._cleanup.aclose()


@dataclass(slots=True)
class CallLifecycleWorkerRuntime:
    settings: Settings
    session_factory: AsyncSessionFactory
    arq_pool: ArqRedis
    observability: Observability
    queue_observer: QueueObserver
    now: Callable[[], datetime]
    _cleanup: RuntimeCleanup

    async def aclose(self) -> None:
        await self._cleanup.aclose()


@dataclass(slots=True)
class BackgroundWorkerRuntime:
    settings: Settings
    session_factory: AsyncSessionFactory
    arq_pool: ArqRedis
    observability: Observability
    queue_observer: QueueObserver
    outbox_handlers: Mapping[str, OutboxHandler]
    now: Callable[[], datetime]
    _cleanup: RuntimeCleanup

    async def aclose(self) -> None:
        await self._cleanup.aclose()


class ApiRuntimeUnavailable(RuntimeError):
    pass


class WorkerRuntimeConfigurationError(RuntimeError):
    pass


def get_api_runtime(app: object) -> ApiRuntime:
    state = getattr(app, "state", None)
    runtime = getattr(state, "runtime", None)
    if not isinstance(runtime, ApiRuntime):
        raise ApiRuntimeUnavailable("API runtime is not initialized")
    return runtime


def require_call_lifecycle_runtime(
    ctx: dict[str, Any],
) -> CallLifecycleWorkerRuntime:
    runtime = ctx.get(WORKER_RUNTIME_KEY)
    if not isinstance(runtime, CallLifecycleWorkerRuntime):
        raise WorkerRuntimeConfigurationError(
            "call-lifecycle worker runtime is not initialized"
        )
    return runtime


def require_background_runtime(ctx: dict[str, Any]) -> BackgroundWorkerRuntime:
    runtime = ctx.get(WORKER_RUNTIME_KEY)
    if not isinstance(runtime, BackgroundWorkerRuntime):
        raise WorkerRuntimeConfigurationError(
            "background worker runtime is not initialized"
        )
    return runtime


def require_worker_observability(ctx: dict[str, Any]) -> Observability:
    runtime = ctx.get(WORKER_RUNTIME_KEY)
    if isinstance(runtime, CallLifecycleWorkerRuntime):
        return runtime.observability
    if isinstance(runtime, BackgroundWorkerRuntime):
        return runtime.observability
    raise WorkerRuntimeConfigurationError("worker runtime is not initialized")
