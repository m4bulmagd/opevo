from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.composition.lifecycle import RuntimeCleanup
from app.core.config import Settings


if TYPE_CHECKING:
    from arq.connections import ArqRedis
    from redis.asyncio import Redis
    from sqlalchemy.ext.asyncio import AsyncEngine

    from app.core.auth import AuthProvider
    from app.core.database import AsyncSessionFactory
    from app.core.observability import Observability
    from app.providers.storage.base import StorageProvider
    from app.routers.readiness import ReadinessChecks
    from app.services.livekit_recording_service import LiveKitRecordingService
    from app.services.realtime_service import RealtimeService
    from app.workers.call_finalization_queue import CallFinalizationQueue


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


class ApiRuntimeUnavailable(RuntimeError):
    pass


def get_api_runtime(app: object) -> ApiRuntime:
    state = getattr(app, "state", None)
    runtime = getattr(state, "runtime", None)
    if not isinstance(runtime, ApiRuntime):
        raise ApiRuntimeUnavailable("API runtime is not initialized")
    return runtime
