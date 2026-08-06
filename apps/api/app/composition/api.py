import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from arq.connections import ArqRedis
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine

from app.composition.lifecycle import RuntimeCleanup
from app.composition.runtime import ApiRuntime
from app.core.auth import AuthProvider, build_auth_provider
from app.core.config import Settings
from app.core.database import create_database_engine, create_session_factory
from app.core.logging import report_safe_exception, setup_logging
from app.core.observability import (
    Observability,
    initialize_observability,
    shutdown_observability,
)
from app.core.redis import RedisEventBus, create_arq_pool
from app.core.runtime_validation import validate_api_runtime
from app.providers.storage.base import StorageProvider
from app.providers.livekit_recording.livekit import LiveKitRecordingProvider
from app.providers.storage.s3 import S3Storage
from app.routers.readiness import ReadinessChecks
from app.services.livekit_recording_service import LiveKitRecordingService
from app.services.realtime_service import RealtimeService
from app.websockets.manager import manager as websocket_manager
from app.workers.call_finalization_queue import CallFinalizationQueue


logger = logging.getLogger(__name__)
ApiRuntimeBuilder = Callable[[Settings], Awaitable[ApiRuntime]]


def _create_redis_client(redis_url: str) -> Redis:
    return Redis.from_url(redis_url, decode_responses=True)


def _create_storage(
    *, settings: Settings, observability: Observability
) -> StorageProvider:
    return S3Storage(
        bucket_name=settings.storage_bucket_name,
        endpoint_url=settings.s3_endpoint_url or "http://minio:9000",
        access_key=settings.s3_access_key,
        secret_key=settings.s3_secret_key,
        region=settings.s3_region,
        observability=observability,
    )


def _create_livekit_webhook_receiver(
    *, settings: Settings, observability: Observability
) -> object:
    del observability
    from livekit import api as livekit_api_module

    verifier = livekit_api_module.TokenVerifier(
        settings.livekit_api_key,
        settings.livekit_api_secret,
    )
    return livekit_api_module.WebhookReceiver(verifier)


def _create_livekit_recording_service(
    *,
    settings: Settings,
    observability: Observability,
    register_owned_resource: Callable[[object], None],
) -> LiveKitRecordingService:
    from livekit import api as livekit_api_module

    livekit_api = livekit_api_module.LiveKitAPI(
        url=settings.livekit_url,
        api_key=settings.livekit_api_key,
        api_secret=settings.livekit_api_secret,
    )
    register_owned_resource(livekit_api)
    provider = LiveKitRecordingProvider(
        egress_client=livekit_api.egress,
        bucket_name=settings.storage_bucket_name,
        endpoint_url=settings.s3_endpoint_url or "http://minio:9000",
        access_key=settings.s3_access_key,
        secret_key=settings.s3_secret_key,
        region=settings.s3_region,
        observability=observability,
    )
    return LiveKitRecordingService(provider)


async def _close_resource(
    resource: object,
    *,
    event: str,
    operation: str,
) -> None:
    try:
        close = getattr(resource, "aclose", None)
        if close is not None:
            await close()
            return
        close = getattr(resource, "close", None)
        if close is not None:
            await close()
            return
        dispose = getattr(resource, "dispose", None)
        if dispose is not None:
            await dispose()
    except Exception as error:
        report_safe_exception(
            logger,
            event=event,
            operation=operation,
            error=error,
            status="failed",
            level=logging.WARNING,
        )
        raise


async def _close_observability(observability: Observability) -> None:
    close = getattr(observability, "aclose", None)
    if close is not None:
        await close()
        return
    await shutdown_observability(observability)


async def _stop_realtime_fanout(relay_task: asyncio.Task[None]) -> None:
    if not relay_task.done():
        relay_task.cancel()
    try:
        await relay_task
    except asyncio.CancelledError:
        pass
    except Exception as error:
        report_safe_exception(
            logger,
            event="realtime_fanout_failed",
            operation="stop_realtime_fanout",
            error=error,
            status="failed",
            level=logging.WARNING,
        )


async def build_api_runtime(
    settings: Settings,
    *,
    engine_factory: Callable[[str], AsyncEngine] = create_database_engine,
    redis_factory: Callable[[str], Redis] = _create_redis_client,
    observability_factory: Callable[..., Observability] = initialize_observability,
    auth_factory: Callable[..., AuthProvider] = build_auth_provider,
    readiness_factory: Callable[..., ReadinessChecks] = ReadinessChecks,
    storage_factory: Callable[..., StorageProvider] = _create_storage,
    arq_pool_factory: Callable[[str], Awaitable[ArqRedis]] = create_arq_pool,
    realtime_service_factory: Callable[..., RealtimeService] = RealtimeService,
    webhook_receiver_factory: Callable[..., object] = _create_livekit_webhook_receiver,
    recording_service_factory: Callable[
        ..., LiveKitRecordingService
    ] = _create_livekit_recording_service,
) -> ApiRuntime:
    validate_api_runtime(settings)
    setup_logging()
    stack = AsyncExitStack()
    await stack.__aenter__()
    try:
        observability = observability_factory(
            service_name=settings.otel_service_name,
            endpoint=settings.otel_exporter_otlp_endpoint,
        )
        stack.push_async_callback(_close_observability, observability)

        engine = engine_factory(settings.database_url)
        stack.push_async_callback(
            _close_resource,
            engine,
            event="database_engine_close_failed",
            operation="dispose_database_engine",
        )
        session_factory = create_session_factory(engine)

        redis_client = redis_factory(settings.redis_url)
        stack.push_async_callback(
            _close_resource,
            redis_client,
            event="redis_client_close_failed",
            operation="close_redis_client",
        )

        auth_provider = auth_factory(
            settings=settings,
            observability=observability,
        )
        stack.push_async_callback(
            _close_resource,
            auth_provider,
            event="auth_provider_close_failed",
            operation="close_auth_provider",
        )
        readiness_checks = readiness_factory(
            engine=engine,
            redis=redis_client,
            observability=observability,
        )

        storage_provider = storage_factory(
            settings=settings,
            observability=observability,
        )
        stack.push_async_callback(
            _close_resource,
            storage_provider,
            event="storage_provider_close_failed",
            operation="close_storage_provider",
        )

        arq_pool: ArqRedis | None = None
        call_finalization_queue: CallFinalizationQueue | None = None
        if settings.app_env != "test":
            arq_pool = await arq_pool_factory(settings.redis_url)
            stack.push_async_callback(
                _close_resource,
                arq_pool,
                event="arq_pool_close_failed",
                operation="close_arq_pool",
            )
            call_finalization_queue = CallFinalizationQueue(arq_pool)

        realtime_service: RealtimeService | None = None
        if settings.realtime_enabled:
            realtime_service = realtime_service_factory(
                auth_provider,
                event_bus=RedisEventBus(redis_client),
                websocket_manager=websocket_manager,
                observability=observability,
            )
            if settings.app_env != "test":
                relay_task = asyncio.create_task(realtime_service.fanout_forever())
                stack.push_async_callback(_stop_realtime_fanout, relay_task)

        livekit_webhook_receiver: object | None = None
        livekit_recording_service: LiveKitRecordingService | None = None
        if (
            settings.livekit_url
            and settings.livekit_api_key
            and settings.livekit_api_secret
        ):
            livekit_webhook_receiver = webhook_receiver_factory(
                settings=settings,
                observability=observability,
            )

            def register_recording_resource(resource: object) -> None:
                stack.push_async_callback(
                    _close_resource,
                    resource,
                    event="livekit_recording_resource_close_failed",
                    operation="close_livekit_recording_resource",
                )

            livekit_recording_service = recording_service_factory(
                settings=settings,
                observability=observability,
                register_owned_resource=register_recording_resource,
            )

        return ApiRuntime(
            settings=settings,
            engine=engine,
            session_factory=session_factory,
            redis_client=redis_client,
            observability=observability,
            auth_provider=auth_provider,
            readiness_checks=readiness_checks,
            storage_provider=storage_provider,
            arq_pool=arq_pool,
            call_finalization_queue=call_finalization_queue,
            realtime_service=realtime_service,
            livekit_webhook_receiver=livekit_webhook_receiver,
            livekit_recording_service=livekit_recording_service,
            _cleanup=RuntimeCleanup(stack),
        )
    except BaseException:
        try:
            await stack.aclose()
        except BaseException as cleanup_error:
            report_safe_exception(
                logger,
                event="api_runtime_partial_cleanup_failed",
                operation="close_partial_api_runtime",
                error=cleanup_error,
                status="failed",
                level=logging.WARNING,
            )
        raise
