from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager
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


class _EngineProbe:
    def __init__(
        self,
        *,
        setup_error: BaseException | None = None,
        dispose_error: BaseException | None = None,
    ) -> None:
        self.setup_error = setup_error
        self.dispose_error = dispose_error
        self.dispose_calls = 0

    @asynccontextmanager
    async def begin(self):
        yield self

    async def execute(self, _statement) -> None:
        if self.setup_error is not None:
            raise self.setup_error

    async def dispose(self) -> None:
        self.dispose_calls += 1
        if self.dispose_error is not None:
            raise self.dispose_error


class _DependencyProbe:
    def __init__(self, close_error: BaseException) -> None:
        self.close_error = close_error
        self.close_calls = 0
        self._session = object()
        self._yielded = False

    async def __anext__(self) -> object:
        if self._yielded:
            raise StopAsyncIteration
        self._yielded = True
        return self._session

    async def aclose(self) -> None:
        self.close_calls += 1
        raise self.close_error


class _RealtimeServiceProbe:
    def __init__(self) -> None:
        self.fanout_calls = 0

    def fanout_forever(self) -> Awaitable[None]:
        self.fanout_calls += 1

        async def fail() -> None:
            raise AssertionError("test environment must not start realtime fanout")

        return fail()


def _forbidden_factory(name: str) -> Callable[..., Any]:
    def forbidden(*_args: object, **_kwargs: object) -> Any:
        raise AssertionError(f"{name} must not be constructed")

    return forbidden


@pytest.mark.anyio
@pytest.mark.parametrize("settings_source", ["constructor", "process"])
async def test_normalized_test_environment_keeps_api_runtime_hermetic(
    settings,
    settings_source: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.composition.api import build_api_runtime
    from app.core.config import Settings

    values = settings.model_dump()
    values.update(
        {
            "realtime_enabled": True,
            "livekit_url": None,
            "livekit_api_key": None,
            "livekit_api_secret": None,
        }
    )
    values.pop("app_env")
    if settings_source == "constructor":
        values["app_env"] = " TEST "
    else:
        monkeypatch.setenv("APP_ENV", " TEST ")
    configured_settings = Settings(**values)
    closed_resources: list[str] = []
    realtime_service = _RealtimeServiceProbe()

    runtime = await build_api_runtime(
        configured_settings,
        engine_factory=lambda _url: _Engine("engine", closed_resources),
        redis_factory=lambda _url: _OwnedResource("redis", closed_resources),
        observability_factory=lambda **_kwargs: _OwnedResource(
            "observability", closed_resources
        ),
        auth_factory=lambda **_kwargs: _OwnedResource("auth", closed_resources),
        readiness_factory=lambda **_kwargs: object(),
        storage_factory=lambda **_kwargs: _OwnedResource(
            "storage", closed_resources
        ),
        arq_pool_factory=_forbidden_factory("ARQ pool"),
        realtime_service_factory=lambda *_args, **_kwargs: realtime_service,
        webhook_receiver_factory=_forbidden_factory("LiveKit webhook receiver"),
        recording_service_factory=_forbidden_factory("recording service"),
    )
    try:
        assert configured_settings.app_env == "test"
        assert runtime.arq_pool is None
        assert runtime.call_finalization_queue is None
        assert runtime.realtime_service is realtime_service
        assert realtime_service.fanout_calls == 0
    finally:
        await runtime.aclose()


@pytest.mark.anyio
async def test_api_composition_rejects_partial_livekit_before_resources_start(
    settings,
) -> None:
    from app.composition.api import build_api_runtime

    partial_settings = settings.model_copy(
        update={
            "app_env": "development",
            "livekit_url": "wss://livekit.example.com",
            "livekit_api_key": "key",
            "livekit_api_secret": None,
        }
    )

    with pytest.raises(RuntimeError, match="LIVEKIT_API_SECRET"):
        await build_api_runtime(
            partial_settings,
            engine_factory=_forbidden_factory("engine"),
            redis_factory=_forbidden_factory("redis"),
            observability_factory=_forbidden_factory("observability"),
            auth_factory=_forbidden_factory("auth"),
            readiness_factory=_forbidden_factory("readiness"),
            storage_factory=_forbidden_factory("storage"),
            arq_pool_factory=_forbidden_factory("ARQ pool"),
            realtime_service_factory=_forbidden_factory("realtime service"),
            webhook_receiver_factory=_forbidden_factory("webhook receiver"),
            recording_service_factory=_forbidden_factory("recording service"),
        )


@asynccontextmanager
async def _sqlite_api_runtime(
    settings,
    database_path,
    *,
    engine_factory=None,
):
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    if engine_factory is None:
        from app.core.database import create_database_engine

        engine_factory = create_database_engine

    class TrackingAsyncSession(AsyncSession):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            self.close_calls = 0

        async def close(self) -> None:
            self.close_calls += 1
            await super().close()

    engine = engine_factory(f"sqlite+aiosqlite:///{database_path}")
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("CREATE TABLE session_probe (value TEXT NOT NULL)")
            )
        session_factory = async_sessionmaker(
            engine,
            class_=TrackingAsyncSession,
            expire_on_commit=False,
        )
        runtime = _api_runtime(
            settings,
            engine=engine,
            session_factory=session_factory,
        )
        yield runtime, session_factory
    except BaseException as operation_error:
        try:
            await engine.dispose()
        except BaseException as cleanup_error:
            raise operation_error from cleanup_error
        raise
    await engine.dispose()


@asynccontextmanager
async def _request_session_lifecycle(
    settings,
    database_path,
    *,
    engine_factory=None,
    dependency_factory=None,
):
    from fastapi import FastAPI
    from starlette.requests import Request

    if dependency_factory is None:
        from app.core.database import get_session

        dependency_factory = get_session

    async with _sqlite_api_runtime(
        settings,
        database_path,
        engine_factory=engine_factory,
    ) as (runtime, session_factory):
        app = FastAPI()
        app.state.runtime = runtime
        dependency = dependency_factory(Request({"type": "http", "app": app}))
        try:
            session = await anext(dependency)
            yield session, session_factory, dependency
        finally:
            await dependency.aclose()


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
async def test_recording_factory_can_register_transport_before_later_failure(
    settings,
) -> None:
    from app.composition.api import build_api_runtime

    configured_settings = settings.model_copy(
        update={
            "app_env": "test",
            "realtime_enabled": False,
            "livekit_url": "wss://livekit.example.com",
            "livekit_api_key": "livekit-key",
            "livekit_api_secret": "livekit-secret",
        }
    )
    closed_resources: list[str] = []
    recording_transport = _OwnedResource("recording_transport", closed_resources)

    def fail_after_transport(
        *, settings, observability, register_owned_resource
    ) -> object:
        del settings, observability
        register_owned_resource(recording_transport)
        raise RuntimeError("recording provider construction failed")

    with pytest.raises(RuntimeError, match="recording provider construction failed"):
        await build_api_runtime(
            configured_settings,
            engine_factory=lambda _url: _Engine("engine", closed_resources),
            redis_factory=lambda _url: _OwnedResource("redis", closed_resources),
            observability_factory=lambda **_kwargs: _OwnedResource(
                "observability", closed_resources
            ),
            auth_factory=lambda **_kwargs: _OwnedResource("auth", closed_resources),
            readiness_factory=lambda **_kwargs: object(),
            storage_factory=lambda **_kwargs: _OwnedResource(
                "storage", closed_resources
            ),
            realtime_service_factory=_forbidden_factory("realtime service"),
            webhook_receiver_factory=lambda **_kwargs: object(),
            recording_service_factory=fail_after_transport,
        )

    assert recording_transport.close_calls == 1
    assert closed_resources[0] == "recording_transport"


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

    from app.routers.calls import get_recording_service

    storage_provider = object()
    app = FastAPI()
    app.state.runtime = _api_runtime(
        settings,
        storage_provider=storage_provider,
    )
    request = Request({"type": "http", "app": app})

    service = get_recording_service(request)

    assert service.provider is storage_provider


@pytest.mark.anyio
async def test_request_session_rejects_missing_api_runtime() -> None:
    from fastapi import FastAPI
    from starlette.requests import Request

    from app.composition.runtime import ApiRuntimeUnavailable
    from app.core.database import get_session

    request = Request({"type": "http", "app": FastAPI()})

    with pytest.raises(
        ApiRuntimeUnavailable,
        match="API runtime is not initialized",
    ):
        dependency = get_session(request)
        await anext(dependency)


@pytest.mark.anyio
@pytest.mark.parametrize("dispose_fails", [False, True])
async def test_sqlite_runtime_disposes_engine_without_masking_setup_failure(
    settings,
    tmp_path,
    dispose_fails: bool,
) -> None:
    class SetupFailure(RuntimeError):
        pass

    class DisposeFailure(RuntimeError):
        pass

    setup_error = SetupFailure("session schema setup failed")
    dispose_error = (
        DisposeFailure("engine dispose failed") if dispose_fails else None
    )
    engine = _EngineProbe(
        setup_error=setup_error,
        dispose_error=dispose_error,
    )

    def engine_factory(database_url: str) -> _EngineProbe:
        assert database_url == f"sqlite+aiosqlite:///{tmp_path / 'setup.db'}"
        return engine

    with pytest.raises(SetupFailure, match="session schema setup failed") as caught:
        async with _sqlite_api_runtime(
            settings,
            tmp_path / "setup.db",
            engine_factory=engine_factory,
        ):
            pytest.fail("runtime must not be yielded after schema setup failure")

    assert caught.value is setup_error
    assert caught.value.__cause__ is dispose_error
    assert engine.dispose_calls == 1


@pytest.mark.anyio
@pytest.mark.parametrize("dispose_fails", [False, True])
async def test_request_session_lifecycle_disposes_engine_when_dependency_close_fails(
    settings,
    tmp_path,
    dispose_fails: bool,
) -> None:
    class DependencyCloseFailure(RuntimeError):
        pass

    class DisposeFailure(RuntimeError):
        pass

    close_error = DependencyCloseFailure("dependency close failed")
    dispose_error = (
        DisposeFailure("engine dispose failed") if dispose_fails else None
    )
    dependency = _DependencyProbe(close_error)
    engine = _EngineProbe(dispose_error=dispose_error)

    def engine_factory(database_url: str) -> _EngineProbe:
        assert database_url == f"sqlite+aiosqlite:///{tmp_path / 'close.db'}"
        return engine

    def dependency_factory(request):
        assert request.app.state.runtime.engine is engine
        return dependency

    with pytest.raises(
        DependencyCloseFailure,
        match="dependency close failed",
    ) as caught:
        async with _request_session_lifecycle(
            settings,
            tmp_path / "close.db",
            engine_factory=engine_factory,
            dependency_factory=dependency_factory,
        ):
            pass

    assert caught.value is close_error
    assert caught.value.__cause__ is dispose_error
    assert dependency.close_calls == 1
    assert engine.dispose_calls == 1


@pytest.mark.anyio
async def test_request_session_persists_committed_work_and_closes(
    settings,
    tmp_path,
) -> None:
    from sqlalchemy import text

    async with _request_session_lifecycle(
        settings,
        tmp_path / "committed.db",
    ) as (session, session_factory, dependency):
        await session.execute(
            text("INSERT INTO session_probe (value) VALUES ('committed')")
        )
        await session.commit()

        await dependency.aclose()

        assert session.close_calls == 1
        async with session_factory() as verification_session:
            result = await verification_session.execute(
                text("SELECT value FROM session_probe")
            )
            assert result.scalar_one() == "committed"


@pytest.mark.anyio
async def test_request_session_rolls_back_and_closes_when_generator_closes(
    settings,
    tmp_path,
) -> None:
    from sqlalchemy import text

    async with _request_session_lifecycle(
        settings,
        tmp_path / "generator-close.db",
    ) as (session, session_factory, dependency):
        await session.execute(
            text("INSERT INTO session_probe (value) VALUES ('rolled-back')")
        )
        assert session.in_transaction() is True

        await dependency.aclose()

        assert session.in_transaction() is False
        assert session.close_calls == 1
        async with session_factory() as verification_session:
            result = await verification_session.execute(
                text("SELECT COUNT(*) FROM session_probe")
            )
            assert result.scalar_one() == 0


@pytest.mark.anyio
async def test_request_session_rolls_back_and_closes_on_handler_failure(
    settings,
    tmp_path,
) -> None:
    from sqlalchemy import text

    async with _request_session_lifecycle(
        settings,
        tmp_path / "handler-failure.db",
    ) as (session, session_factory, dependency):
        await session.execute(
            text("INSERT INTO session_probe (value) VALUES ('rolled-back')")
        )

        with pytest.raises(RuntimeError, match="handler failed"):
            await dependency.athrow(RuntimeError("handler failed"))

        assert session.in_transaction() is False
        assert session.close_calls == 1
        async with session_factory() as verification_session:
            result = await verification_session.execute(
                text("SELECT COUNT(*) FROM session_probe")
            )
            assert result.scalar_one() == 0


@pytest.mark.anyio
async def test_shared_test_app_always_uses_isolated_tmp_path_sqlite(
    settings,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import conftest

    from app.core.database import get_session

    monkeypatch.setenv(
        "CLIENT_TEST_DATABASE_URL",
        f"sqlite+aiosqlite:///{tmp_path / 'persistent.db'}",
    )
    fixture = conftest.test_app.__wrapped__(tmp_path, settings)
    application = await anext(fixture)
    try:
        assert application.state.runtime.settings.database_url == (
            f"sqlite+aiosqlite:///{tmp_path / 'test_client.db'}"
        )
        assert get_session not in application.dependency_overrides
    finally:
        await fixture.aclose()


@pytest.mark.anyio
@pytest.mark.parametrize("dispose_fails", [False, True])
async def test_test_database_setup_disposes_engine_without_masking_setup_failure(
    dispose_fails: bool,
) -> None:
    import conftest

    class SetupFailure(RuntimeError):
        pass

    class DisposeFailure(RuntimeError):
        pass

    class Connection:
        async def run_sync(self, _operation) -> None:
            raise SetupFailure("schema setup failed")

    class BeginContext:
        async def __aenter__(self) -> Connection:
            return Connection()

        async def __aexit__(self, *_args: object) -> None:
            return None

    class Engine:
        def __init__(self) -> None:
            self.dispose_calls = 0

        def begin(self) -> BeginContext:
            return BeginContext()

        async def dispose(self) -> None:
            self.dispose_calls += 1
            if dispose_fails:
                raise DisposeFailure("dispose failed")

    engine = Engine()

    def engine_factory(database_url: str) -> Engine:
        assert database_url == "sqlite+aiosqlite:///isolated.db"
        return engine

    with pytest.raises(SetupFailure, match="schema setup failed"):
        await conftest._initialize_test_database(
            "sqlite+aiosqlite:///isolated.db",
            engine_factory=engine_factory,
        )

    assert engine.dispose_calls == 1
