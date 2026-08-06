from collections.abc import Callable
from contextlib import AsyncExitStack
from typing import Any

import pytest


def _api_runtime(settings, **overrides):
    from app.composition.lifecycle import RuntimeCleanup
    from app.composition.runtime import ApiRuntime

    values = {
        "settings": settings,
        "engine": object(),
        "session_factory": object(),
        "redis_client": object(),
        "observability": object(),
        "auth_provider": object(),
        "readiness_checks": object(),
        "storage_provider": object(),
        "arq_pool": None,
        "call_finalization_queue": None,
        "realtime_service": None,
        "livekit_webhook_receiver": None,
        "livekit_recording_service": None,
        "_cleanup": RuntimeCleanup(AsyncExitStack()),
    }
    values.update(overrides)
    return ApiRuntime(**values)


class _OwnedResource:
    def __init__(
        self,
        name: str,
        closed_resources: list[str],
        *,
        close_error: Exception | None = None,
    ) -> None:
        self.name = name
        self.closed_resources = closed_resources
        self.close_error = close_error
        self.close_calls = 0

    async def aclose(self) -> None:
        self.close_calls += 1
        self.closed_resources.append(self.name)
        if self.close_error is not None:
            raise self.close_error


class _Engine(_OwnedResource):
    async def dispose(self) -> None:
        await self.aclose()


def _forbidden_factory(name: str) -> Callable[..., Any]:
    def forbidden(*_args: object, **_kwargs: object) -> Any:
        raise AssertionError(f"{name} must not be constructed")

    return forbidden


@pytest.mark.anyio
async def test_build_api_runtime_owns_resources_and_closes_once_in_reverse_order(
    settings,
) -> None:
    from app.composition.api import build_api_runtime

    configured_settings = settings.model_copy(
        update={
            "app_env": "development",
            "realtime_enabled": False,
            "livekit_url": None,
            "livekit_api_key": None,
            "livekit_api_secret": None,
        }
    )
    closed_resources: list[str] = []
    observability = _OwnedResource("observability", closed_resources)
    engine = _Engine("engine", closed_resources)
    redis_client = _OwnedResource("redis", closed_resources)
    auth_provider = _OwnedResource("auth", closed_resources)
    readiness_checks = object()
    storage_provider = _OwnedResource("storage", closed_resources)
    arq_pool = _OwnedResource("arq_pool", closed_resources)

    def engine_factory(database_url: str) -> _Engine:
        assert database_url == configured_settings.database_url
        return engine

    def redis_factory(redis_url: str) -> _OwnedResource:
        assert redis_url == configured_settings.redis_url
        return redis_client

    def observability_factory(*, service_name: str, endpoint: str | None):
        assert service_name == configured_settings.otel_service_name
        assert endpoint == configured_settings.otel_exporter_otlp_endpoint
        return observability

    def auth_factory(*, settings, observability):
        assert settings is configured_settings
        assert observability is globals_observability
        return auth_provider

    def readiness_factory(*, engine, redis, observability):
        assert engine is globals_engine
        assert redis is redis_client
        assert observability is globals_observability
        return readiness_checks

    def storage_factory(*, settings, observability):
        assert settings is configured_settings
        assert observability is globals_observability
        return storage_provider

    async def arq_pool_factory(redis_url: str):
        assert redis_url == configured_settings.redis_url
        return arq_pool

    globals_observability = observability
    globals_engine = engine
    runtime = await build_api_runtime(
        configured_settings,
        engine_factory=engine_factory,
        redis_factory=redis_factory,
        observability_factory=observability_factory,
        auth_factory=auth_factory,
        readiness_factory=readiness_factory,
        storage_factory=storage_factory,
        arq_pool_factory=arq_pool_factory,
        realtime_service_factory=_forbidden_factory("realtime service"),
        webhook_receiver_factory=_forbidden_factory("LiveKit webhook receiver"),
        recording_service_factory=_forbidden_factory("recording service"),
    )

    assert runtime.settings is configured_settings
    assert runtime.engine is engine
    assert runtime.redis_client is redis_client
    assert runtime.auth_provider is auth_provider
    assert runtime.readiness_checks is readiness_checks
    assert runtime.storage_provider is storage_provider
    assert runtime.arq_pool is arq_pool
    assert runtime.call_finalization_queue.redis is arq_pool
    assert runtime.realtime_service is None
    assert runtime.livekit_webhook_receiver is None
    assert runtime.livekit_recording_service is None

    await runtime.aclose()
    await runtime.aclose()

    assert closed_resources == [
        "arq_pool",
        "storage",
        "auth",
        "redis",
        "engine",
        "observability",
    ]
    assert all(
        resource.close_calls == 1
        for resource in (
            arq_pool,
            storage_provider,
            auth_provider,
            redis_client,
            engine,
            observability,
        )
    )


@pytest.mark.anyio
async def test_build_api_runtime_unwinds_every_open_resource_on_late_failure(
    settings,
) -> None:
    from app.composition.api import build_api_runtime

    configured_settings = settings.model_copy(
        update={
            "app_env": "development",
            "realtime_enabled": False,
            "livekit_url": "wss://livekit.example.com",
            "livekit_api_key": "livekit-key",
            "livekit_api_secret": "livekit-secret",
        }
    )
    closed_resources: list[str] = []
    observability = _OwnedResource("observability", closed_resources)
    engine = _Engine("engine", closed_resources)
    redis_client = _OwnedResource("redis", closed_resources)
    auth_provider = _OwnedResource("auth", closed_resources)
    storage_provider = _OwnedResource("storage", closed_resources)
    arq_pool = _OwnedResource("arq_pool", closed_resources)

    def fail_webhook_receiver(*, settings, observability):
        assert settings is configured_settings
        assert observability is globals_observability
        raise RuntimeError("late construction failure")

    async def arq_pool_factory(_redis_url: str):
        return arq_pool

    globals_observability = observability
    with pytest.raises(RuntimeError, match="late construction failure"):
        await build_api_runtime(
            configured_settings,
            engine_factory=lambda _url: engine,
            redis_factory=lambda _url: redis_client,
            observability_factory=lambda **_kwargs: observability,
            auth_factory=lambda **_kwargs: auth_provider,
            readiness_factory=lambda **_kwargs: object(),
            storage_factory=lambda **_kwargs: storage_provider,
            arq_pool_factory=arq_pool_factory,
            realtime_service_factory=_forbidden_factory("realtime service"),
            webhook_receiver_factory=fail_webhook_receiver,
            recording_service_factory=_forbidden_factory("recording service"),
        )

    assert closed_resources == [
        "arq_pool",
        "storage",
        "auth",
        "redis",
        "engine",
        "observability",
    ]
    assert all(
        resource.close_calls == 1
        for resource in (
            arq_pool,
            storage_provider,
            auth_provider,
            redis_client,
            engine,
            observability,
        )
    )


@pytest.mark.anyio
async def test_runtime_close_attempts_every_resource_and_reports_close_failure(
    settings,
) -> None:
    from app.composition.api import build_api_runtime

    configured_settings = settings.model_copy(
        update={
            "app_env": "development",
            "realtime_enabled": False,
            "livekit_url": None,
            "livekit_api_key": None,
            "livekit_api_secret": None,
        }
    )
    closed_resources: list[str] = []
    observability = _OwnedResource("observability", closed_resources)
    engine = _Engine("engine", closed_resources)
    redis_client = _OwnedResource("redis", closed_resources)
    auth_provider = _OwnedResource("auth", closed_resources)
    storage_provider = _OwnedResource(
        "storage",
        closed_resources,
        close_error=RuntimeError("storage close failed"),
    )
    arq_pool = _OwnedResource("arq_pool", closed_resources)

    async def arq_pool_factory(_redis_url: str):
        return arq_pool

    runtime = await build_api_runtime(
        configured_settings,
        engine_factory=lambda _url: engine,
        redis_factory=lambda _url: redis_client,
        observability_factory=lambda **_kwargs: observability,
        auth_factory=lambda **_kwargs: auth_provider,
        readiness_factory=lambda **_kwargs: object(),
        storage_factory=lambda **_kwargs: storage_provider,
        arq_pool_factory=arq_pool_factory,
        realtime_service_factory=_forbidden_factory("realtime service"),
        webhook_receiver_factory=_forbidden_factory("LiveKit webhook receiver"),
        recording_service_factory=_forbidden_factory("recording service"),
    )

    with pytest.raises(RuntimeError, match="storage close failed"):
        await runtime.aclose()

    assert closed_resources == [
        "arq_pool",
        "storage",
        "auth",
        "redis",
        "engine",
        "observability",
    ]


@pytest.mark.anyio
async def test_partial_startup_preserves_construction_failure_when_cleanup_fails(
    settings,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from app.composition.api import build_api_runtime

    configured_settings = settings.model_copy(
        update={
            "app_env": "development",
            "realtime_enabled": False,
            "livekit_url": "wss://livekit.example.com",
            "livekit_api_key": "livekit-key",
            "livekit_api_secret": "livekit-secret",
        }
    )
    closed_resources: list[str] = []
    observability = _OwnedResource("observability", closed_resources)
    engine = _Engine("engine", closed_resources)
    redis_client = _OwnedResource("redis", closed_resources)
    auth_provider = _OwnedResource("auth", closed_resources)
    storage_provider = _OwnedResource(
        "storage",
        closed_resources,
        close_error=RuntimeError("sensitive cleanup detail"),
    )
    arq_pool = _OwnedResource("arq_pool", closed_resources)

    async def arq_pool_factory(_redis_url: str):
        return arq_pool

    def fail_webhook_receiver(**_kwargs: object) -> object:
        raise RuntimeError("late construction failure")

    with caplog.at_level("WARNING"):
        with pytest.raises(RuntimeError, match="late construction failure"):
            await build_api_runtime(
                configured_settings,
                engine_factory=lambda _url: engine,
                redis_factory=lambda _url: redis_client,
                observability_factory=lambda **_kwargs: observability,
                auth_factory=lambda **_kwargs: auth_provider,
                readiness_factory=lambda **_kwargs: object(),
                storage_factory=lambda **_kwargs: storage_provider,
                arq_pool_factory=arq_pool_factory,
                realtime_service_factory=_forbidden_factory("realtime service"),
                webhook_receiver_factory=fail_webhook_receiver,
                recording_service_factory=_forbidden_factory("recording service"),
            )

    assert closed_resources == [
        "arq_pool",
        "storage",
        "auth",
        "redis",
        "engine",
        "observability",
    ]
    assert "event=storage_provider_close_failed" in caplog.text
    assert "sensitive cleanup detail" not in caplog.text


@pytest.mark.anyio
async def test_build_api_runtime_validates_before_opening_resources(settings) -> None:
    from app.composition.api import build_api_runtime

    invalid_settings = settings.model_copy(
        update={"auth_mode": "local", "local_auth_token": ""}
    )
    constructed: list[str] = []

    def forbidden_factory(*_args: object, **_kwargs: object) -> object:
        constructed.append("resource")
        return object()

    with pytest.raises(RuntimeError, match="LOCAL_AUTH_TOKEN"):
        await build_api_runtime(
            invalid_settings,
            engine_factory=forbidden_factory,
            redis_factory=forbidden_factory,
            observability_factory=forbidden_factory,
            auth_factory=forbidden_factory,
            readiness_factory=forbidden_factory,
            storage_factory=forbidden_factory,
            arq_pool_factory=forbidden_factory,
            realtime_service_factory=forbidden_factory,
            webhook_receiver_factory=forbidden_factory,
            recording_service_factory=forbidden_factory,
        )

    assert constructed == []


@pytest.mark.anyio
async def test_create_app_publishes_only_complete_runtime_during_lifespan(
    settings,
) -> None:
    from app.main import create_app

    configured_settings = settings.model_copy(
        update={"auth_mode": "local", "local_auth_token": ""}
    )
    build_calls: list[object] = []

    class Cleanup:
        def __init__(self) -> None:
            self.close_calls = 0

        async def aclose(self) -> None:
            self.close_calls += 1

    cleanup = Cleanup()
    runtime = _api_runtime(configured_settings, _cleanup=cleanup)

    async def runtime_builder(settings_arg):
        build_calls.append(settings_arg)
        return runtime

    app = create_app(configured_settings, runtime_builder=runtime_builder)

    assert app.state.runtime is None
    for legacy_name in (
        "settings",
        "auth_provider",
        "readiness_checks",
        "realtime_service",
        "livekit_webhook_receiver",
        "call_finalization_queue",
        "arq_pool",
        "observability",
    ):
        assert not hasattr(app.state, legacy_name)

    async with app.router.lifespan_context(app):
        assert app.state.runtime is runtime
        assert build_calls == [configured_settings]

    assert app.state.runtime is None
    assert cleanup.close_calls == 1


def test_api_resource_accessors_read_the_runtime_as_the_only_source(settings) -> None:
    from fastapi import FastAPI
    from starlette.requests import Request

    from app.core.auth import get_auth_provider
    from app.routers.agent import get_call_finalization_queue
    from app.routers.readiness import get_readiness_checks
    from app.webhooks.livekit import (
        get_realtime_service,
        get_recording_service,
        get_webhook_receiver,
    )

    auth_provider = object()
    readiness_checks = object()
    realtime_service = object()
    webhook_receiver = object()
    recording_service = object()
    call_finalization_queue = object()
    runtime = _api_runtime(
        settings,
        auth_provider=auth_provider,
        readiness_checks=readiness_checks,
        realtime_service=realtime_service,
        livekit_webhook_receiver=webhook_receiver,
        livekit_recording_service=recording_service,
        call_finalization_queue=call_finalization_queue,
    )
    app = FastAPI()
    app.state.runtime = runtime
    request = Request({"type": "http", "app": app})

    assert get_auth_provider(request) is auth_provider
    assert get_readiness_checks(request) is readiness_checks
    assert get_realtime_service(request) is realtime_service
    assert get_webhook_receiver(request) is webhook_receiver
    assert get_recording_service(request) is recording_service
    assert get_call_finalization_queue(request) is call_finalization_queue


def test_dashboard_settings_dependency_reads_runtime_settings(settings) -> None:
    from fastapi import FastAPI
    from starlette.requests import Request

    from app.routers.dashboard import get_dashboard_settings

    configured_settings = settings.model_copy(
        update={"dashboard_metrics_reference_time": None}
    )
    app = FastAPI()
    app.state.runtime = _api_runtime(configured_settings)
    request = Request({"type": "http", "app": app})

    assert get_dashboard_settings(request) is configured_settings


def test_recording_service_dependency_borrows_runtime_storage(settings) -> None:
    from fastapi import FastAPI
    from starlette.requests import Request

    from app.services.recording_service import get_recording_service

    storage_provider = object()
    app = FastAPI()
    app.state.runtime = _api_runtime(
        settings,
        storage_provider=storage_provider,
    )
    request = Request({"type": "http", "app": app})

    service = get_recording_service(request)

    assert service.provider is storage_provider
