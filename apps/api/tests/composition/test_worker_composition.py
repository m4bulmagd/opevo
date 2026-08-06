import asyncio
from contextlib import AsyncExitStack
from dataclasses import fields
from datetime import UTC, datetime
import logging
import traceback
from typing import Any

import pytest

from app.composition.lifecycle import RuntimeCleanup
from app.composition.runtime import (
    WORKER_RUNTIME_KEY,
    BackgroundWorkerRuntime,
    CallLifecycleWorkerRuntime,
    WorkerRuntimeConfigurationError,
    require_background_runtime,
    require_call_lifecycle_runtime,
    require_worker_observability,
)
from app.composition.workers import (
    build_background_worker_runtime,
    build_call_lifecycle_worker_runtime,
)
from app.core.config import Settings
from app.core.runtime_validation import (
    validate_background_worker_runtime,
    validate_call_lifecycle_worker_runtime,
)


class _BorrowedRedis:
    def __init__(self) -> None:
        self.aclose_calls = 0
        self.close_calls = 0

    async def aclose(self) -> None:
        self.aclose_calls += 1

    async def close(self) -> None:
        self.close_calls += 1


class _Resource:
    def __init__(
        self,
        name: str,
        events: list[str],
        *,
        close_error: BaseException | None = None,
        close_started: asyncio.Event | None = None,
        close_release: asyncio.Event | None = None,
    ) -> None:
        self.name = name
        self.events = events
        self.close_error = close_error
        self.close_started = close_started
        self.close_release = close_release
        self.close_calls = 0

    async def aclose(self) -> None:
        self.close_calls += 1
        self.events.append(f"{self.name}.close:start")
        if self.close_started is not None:
            self.close_started.set()
        if self.close_release is not None:
            await self.close_release.wait()
        self.events.append(f"{self.name}.close:end")
        if self.close_error is not None:
            raise self.close_error


class _Engine(_Resource):
    async def dispose(self) -> None:
        await self.aclose()


class _Observer(_Resource):
    def __init__(self, name: str, events: list[str], **kwargs: Any) -> None:
        super().__init__(name, events, **kwargs)
        self.start_calls = 0
        self.start_error: BaseException | None = None

    def start(self) -> None:
        self.start_calls += 1
        self.events.append(f"{self.name}.start")
        if self.start_error is not None:
            raise self.start_error


def _test_settings(**updates: Any) -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        database_url="sqlite+aiosqlite://",
        redis_url="redis://worker.invalid/0",
        **updates,
    )


def _background_settings(**updates: Any) -> Settings:
    values: dict[str, Any] = {
        "app_env": "development",
        "database_url": "postgresql+asyncpg://db/worker",
        "redis_url": "rediss://redis/0",
        "agent_dispatch_jwt_secret": (
            "background-dispatch-secret-with-at-least-32-bytes"
        ),
        "livekit_url": "wss://livekit.example.com",
        "livekit_api_key": "livekit-key",
        "livekit_api_secret": "livekit-secret",
        "storage_bucket_name": "recordings",
        "s3_endpoint_url": "https://storage.example.com",
        "s3_access_key": "storage-key",
        "s3_secret_key": "storage-secret",
        "s3_region": "eu-west-3",
        "summary_provider": "gemini",
        "summary_model": "gemini-2.5-flash",
        "gemini_api_key": "gemini-key",
        "billing_mode": "fake",
        "telephony_mode": "fake",
    }
    values.update(updates)
    return Settings(_env_file=None, **values)


def _runtime(runtime_type: type[CallLifecycleWorkerRuntime] | type[BackgroundWorkerRuntime]):
    cleanup = RuntimeCleanup(AsyncExitStack())
    common = {
        "settings": _test_settings(),
        "session_factory": object(),
        "arq_pool": object(),
        "observability": object(),
        "queue_observer": object(),
        "now": lambda: datetime(2026, 8, 6, tzinfo=UTC),
        "_cleanup": cleanup,
    }
    if runtime_type is BackgroundWorkerRuntime:
        return BackgroundWorkerRuntime(outbox_handlers={}, **common)
    return CallLifecycleWorkerRuntime(**common)


def test_worker_runtime_shapes_are_distinct_and_exact() -> None:
    assert {field.name for field in fields(CallLifecycleWorkerRuntime)} == {
        "settings",
        "session_factory",
        "arq_pool",
        "observability",
        "queue_observer",
        "now",
        "_cleanup",
    }
    assert {field.name for field in fields(BackgroundWorkerRuntime)} == {
        "settings",
        "session_factory",
        "arq_pool",
        "observability",
        "queue_observer",
        "outbox_handlers",
        "now",
        "_cleanup",
    }
    assert CallLifecycleWorkerRuntime.__mro__ == (
        CallLifecycleWorkerRuntime,
        object,
    )
    assert BackgroundWorkerRuntime.__mro__ == (BackgroundWorkerRuntime, object)


@pytest.mark.parametrize("ctx", [{}, {WORKER_RUNTIME_KEY: object()}])
def test_lifecycle_accessor_rejects_missing_or_wrong_runtime(ctx: dict[str, object]) -> None:
    with pytest.raises(WorkerRuntimeConfigurationError, match="call-lifecycle"):
        require_call_lifecycle_runtime(ctx)


def test_lifecycle_accessor_rejects_background_runtime() -> None:
    with pytest.raises(WorkerRuntimeConfigurationError, match="call-lifecycle"):
        require_call_lifecycle_runtime(
            {WORKER_RUNTIME_KEY: _runtime(BackgroundWorkerRuntime)}
        )


@pytest.mark.parametrize("ctx", [{}, {WORKER_RUNTIME_KEY: object()}])
def test_background_accessor_rejects_missing_or_wrong_runtime(
    ctx: dict[str, object],
) -> None:
    with pytest.raises(WorkerRuntimeConfigurationError, match="background"):
        require_background_runtime(ctx)


def test_background_accessor_rejects_lifecycle_runtime() -> None:
    with pytest.raises(WorkerRuntimeConfigurationError, match="background"):
        require_background_runtime(
            {WORKER_RUNTIME_KEY: _runtime(CallLifecycleWorkerRuntime)}
        )


def test_worker_observability_requires_one_of_the_two_concrete_runtimes() -> None:
    lifecycle = _runtime(CallLifecycleWorkerRuntime)
    background = _runtime(BackgroundWorkerRuntime)

    assert (
        require_worker_observability({WORKER_RUNTIME_KEY: lifecycle})
        is lifecycle.observability
    )
    assert (
        require_worker_observability({WORKER_RUNTIME_KEY: background})
        is background.observability
    )
    with pytest.raises(WorkerRuntimeConfigurationError, match="worker"):
        require_worker_observability({WORKER_RUNTIME_KEY: object()})


def test_lifecycle_validation_accepts_only_lifecycle_owned_configuration() -> None:
    settings = Settings(
        _env_file=None,
        app_env="production",
        database_url="postgresql+asyncpg://db/lifecycle",
        redis_url="rediss://redis/0",
        clerk_issuer="",
        stripe_secret_key="",
        telnyx_api_key="",
        livekit_url="",
        storage_bucket_name="",
        summary_provider="",
    )

    validate_call_lifecycle_worker_runtime(settings)


@pytest.mark.parametrize(
    ("field_name", "setting_name"),
    [("database_url", "DATABASE_URL"), ("redis_url", "REDIS_URL")],
)
def test_each_worker_validator_requires_its_infrastructure_without_values(
    field_name: str,
    setting_name: str,
) -> None:
    sentinel = f"PRIVATE_{setting_name}_VALUE"
    lifecycle = Settings(
        _env_file=None,
        app_env="production",
        database_url="postgresql+asyncpg://db/lifecycle",
        redis_url="rediss://redis/0",
    ).model_copy(update={field_name: sentinel})

    for validator, settings in (
        (
            validate_call_lifecycle_worker_runtime,
            lifecycle.model_copy(update={field_name: ""}),
        ),
        (
            validate_background_worker_runtime,
            _background_settings(**{field_name: ""}),
        ),
    ):
        with pytest.raises(RuntimeError, match=setting_name) as caught:
            validator(settings)
        assert sentinel not in str(caught.value)


@pytest.mark.parametrize(
    ("field_name", "setting_name"),
    [
        ("agent_dispatch_jwt_secret", "AGENT_DISPATCH_JWT_SECRET"),
        ("livekit_url", "LIVEKIT_URL"),
        ("livekit_api_key", "LIVEKIT_API_KEY"),
        ("livekit_api_secret", "LIVEKIT_API_SECRET"),
        ("storage_bucket_name", "STORAGE_BUCKET_NAME"),
        ("s3_endpoint_url", "S3_ENDPOINT_URL"),
        ("s3_access_key", "S3_ACCESS_KEY"),
        ("s3_secret_key", "S3_SECRET_KEY"),
        ("s3_region", "S3_REGION"),
        ("summary_provider", "SUMMARY_PROVIDER"),
        ("summary_model", "SUMMARY_MODEL"),
        ("gemini_api_key", "GEMINI_API_KEY"),
    ],
)
def test_background_validation_requires_dispatch_livekit_storage_and_summary(
    field_name: str,
    setting_name: str,
) -> None:
    settings = _background_settings(**{field_name: ""})

    with pytest.raises(RuntimeError, match=setting_name):
        validate_background_worker_runtime(settings)


@pytest.mark.parametrize("app_env", ["development", "staging", "production"])
@pytest.mark.parametrize("invalid_name", [None, "", " \t"])
def test_runnable_background_requires_a_named_livekit_agent_safely(
    app_env: str,
    invalid_name: str | None,
) -> None:
    production_modes: dict[str, Any] = {}
    if app_env == "production":
        production_modes = {
            "billing_mode": "stripe",
            "stripe_secret_key": "stripe-secret",
            "telephony_mode": "telnyx",
            "telnyx_api_key": "telnyx-key",
            "telnyx_active_connection_id": "active-connection",
            "telnyx_disabled_connection_id": "disabled-connection",
            "telnyx_ordering_enabled": True,
        }
    settings = _background_settings(
        app_env=app_env,
        **production_modes,
    ).model_copy(update={"livekit_agent_name": invalid_name})

    with pytest.raises(RuntimeError) as caught:
        validate_background_worker_runtime(settings)

    assert str(caught.value) == (
        "Missing or invalid required runtime settings: LIVEKIT_AGENT_NAME"
    )
    assert repr(invalid_name) not in str(caught.value)


def test_background_test_mode_skips_external_provider_configuration() -> None:
    validate_background_worker_runtime(_test_settings())


def test_selected_stripe_requires_only_its_worker_credential() -> None:
    configured = _background_settings(
        billing_mode="stripe",
        stripe_secret_key="stripe-secret",
        stripe_webhook_secret="",
        stripe_price_starter="",
    )
    validate_background_worker_runtime(configured)

    with pytest.raises(RuntimeError, match="STRIPE_SECRET_KEY"):
        validate_background_worker_runtime(
            configured.model_copy(update={"stripe_secret_key": ""})
        )


def test_selected_telnyx_requires_only_its_worker_credentials() -> None:
    configured = _background_settings(
        telephony_mode="telnyx",
        telnyx_api_key="telnyx-key",
        telnyx_active_connection_id="active-connection",
        telnyx_disabled_connection_id="disabled-connection",
    )
    validate_background_worker_runtime(configured)

    for field_name, setting_name in (
        ("telnyx_api_key", "TELNYX_API_KEY"),
        ("telnyx_active_connection_id", "TELNYX_ACTIVE_CONNECTION_ID"),
        ("telnyx_disabled_connection_id", "TELNYX_DISABLED_CONNECTION_ID"),
    ):
        with pytest.raises(RuntimeError, match=setting_name):
            validate_background_worker_runtime(
                configured.model_copy(update={field_name: ""})
            )


def test_named_fake_modes_skip_stripe_and_telnyx_credentials() -> None:
    validate_background_worker_runtime(
        _background_settings(
            billing_mode="fake",
            stripe_secret_key="",
            telephony_mode="fake",
            telnyx_api_key="",
            telnyx_active_connection_id="",
            telnyx_disabled_connection_id="",
        )
    )


@pytest.mark.parametrize(
    ("field_name", "setting_name"),
    [("billing_mode", "BILLING_MODE"), ("telephony_mode", "TELEPHONY_MODE")],
)
def test_production_background_rejects_fake_provider_modes_safely(
    field_name: str,
    setting_name: str,
) -> None:
    settings = _background_settings(
        app_env="production",
        billing_mode="stripe",
        stripe_secret_key="stripe-secret",
        telephony_mode="telnyx",
        telnyx_api_key="telnyx-key",
        telnyx_active_connection_id="active-connection",
        telnyx_disabled_connection_id="disabled-connection",
        telnyx_ordering_enabled=True,
    ).model_copy(update={field_name: "fake"})

    with pytest.raises(RuntimeError, match=setting_name) as caught:
        validate_background_worker_runtime(settings)
    assert "fake" not in str(caught.value)


def test_background_rejects_unknown_summary_provider_without_echoing_value() -> None:
    sentinel = "PRIVATE_SUMMARY_PROVIDER_SENTINEL"

    with pytest.raises(RuntimeError, match="SUMMARY_PROVIDER") as caught:
        validate_background_worker_runtime(
            _background_settings(summary_provider=sentinel)
        )

    assert sentinel not in str(caught.value)


def _builder_dependencies(
    events: list[str],
    *,
    observer: _Observer | None = None,
    handlers: dict[str, object] | None = None,
) -> tuple[dict[str, Any], _Resource, _Engine, _Observer, dict[str, object]]:
    telemetry = _Resource("telemetry", events)
    engine = _Engine("engine", events)
    observer = observer or _Observer("observer", events)
    handlers = handlers or {"topic": object()}

    def observability_factory(**_kwargs: Any) -> _Resource:
        events.append("telemetry.create")
        return telemetry

    def engine_factory(_database_url: str) -> _Engine:
        events.append("engine.create")
        return engine

    def session_factory_factory(actual_engine: object) -> object:
        assert actual_engine is engine
        events.append("session_factory.create")
        return object()

    def outbox_handlers_factory() -> dict[str, object]:
        events.append("handlers.create")
        return handlers

    def observer_factory(redis: object, actual_telemetry: object, **kwargs: Any):
        assert isinstance(redis, _BorrowedRedis)
        assert actual_telemetry is telemetry
        events.append(f"observer.create:{kwargs['queue_class']}")
        return observer

    return (
        {
            "engine_factory": engine_factory,
            "session_factory_factory": session_factory_factory,
            "observability_factory": observability_factory,
            "observer_factory": observer_factory,
            "outbox_handlers_factory": outbox_handlers_factory,
        },
        telemetry,
        engine,
        observer,
        handlers,
    )


@pytest.mark.anyio
@pytest.mark.parametrize("builder_name", ["lifecycle", "background"])
async def test_worker_runtime_owns_resources_once_in_reverse_order_but_borrows_redis(
    builder_name: str,
) -> None:
    events: list[str] = []
    redis = _BorrowedRedis()
    dependencies, telemetry, engine, observer, handlers = _builder_dependencies(events)
    settings = _test_settings()
    if builder_name == "lifecycle":
        dependencies.pop("outbox_handlers_factory")
        runtime = await build_call_lifecycle_worker_runtime(
            settings,
            arq_redis=redis,
            **dependencies,
        )
        assert isinstance(runtime, CallLifecycleWorkerRuntime)
    else:
        runtime = await build_background_worker_runtime(
            settings,
            arq_redis=redis,
            **dependencies,
        )
        assert isinstance(runtime, BackgroundWorkerRuntime)
        assert runtime.outbox_handlers is handlers

    await asyncio.gather(runtime.aclose(), runtime.aclose())
    await runtime.aclose()

    assert events[-6:] == [
        "observer.close:start",
        "observer.close:end",
        "engine.close:start",
        "engine.close:end",
        "telemetry.close:start",
        "telemetry.close:end",
    ]
    assert observer.close_calls == engine.close_calls == telemetry.close_calls == 1
    assert redis.aclose_calls == redis.close_calls == 0


@pytest.mark.anyio
async def test_worker_runtime_cleanup_continues_after_waiter_cancellation() -> None:
    events: list[str] = []
    started = asyncio.Event()
    release = asyncio.Event()
    observer = _Observer(
        "observer",
        events,
        close_started=started,
        close_release=release,
    )
    dependencies, telemetry, engine, _observer, _handlers = _builder_dependencies(
        events,
        observer=observer,
    )
    dependencies.pop("outbox_handlers_factory")
    runtime = await build_call_lifecycle_worker_runtime(
        _test_settings(),
        arq_redis=_BorrowedRedis(),
        **dependencies,
    )

    waiter = asyncio.create_task(runtime.aclose())
    await started.wait()
    waiter.cancel("worker-shutdown-cancelled")
    with pytest.raises(asyncio.CancelledError) as caught:
        await waiter
    release.set()
    await runtime.aclose()

    assert caught.value.args == ("worker-shutdown-cancelled",)
    assert observer.close_calls == engine.close_calls == telemetry.close_calls == 1


@pytest.mark.anyio
@pytest.mark.parametrize(
    "close_error",
    [RuntimeError("PRIVATE_CLOSE_ERROR"), asyncio.CancelledError("close-cancelled")],
)
async def test_worker_runtime_cleanup_attempts_all_resources_and_propagates_failure(
    close_error: BaseException,
) -> None:
    events: list[str] = []
    observer = _Observer("observer", events, close_error=close_error)
    dependencies, telemetry, engine, _observer, _handlers = _builder_dependencies(
        events,
        observer=observer,
    )
    dependencies.pop("outbox_handlers_factory")
    runtime = await build_call_lifecycle_worker_runtime(
        _test_settings(),
        arq_redis=_BorrowedRedis(),
        **dependencies,
    )

    with pytest.raises(type(close_error)) as caught:
        await runtime.aclose()

    if not isinstance(close_error, asyncio.CancelledError):
        assert caught.value is close_error
    assert observer.close_calls == engine.close_calls == telemetry.close_calls == 1


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("builder_name", "failure_stage"),
    [
        ("lifecycle", "observability"),
        ("lifecycle", "engine"),
        ("lifecycle", "session_factory"),
        ("lifecycle", "observer"),
        ("lifecycle", "observer_start"),
        ("background", "observability"),
        ("background", "engine"),
        ("background", "session_factory"),
        ("background", "handlers"),
        ("background", "observer"),
        ("background", "observer_start"),
    ],
)
async def test_each_worker_construction_failure_unwinds_all_prior_resources(
    builder_name: str,
    failure_stage: str,
) -> None:
    events: list[str] = []
    redis = _BorrowedRedis()
    dependencies, telemetry, engine, observer, _handlers = _builder_dependencies(events)
    construction_error = RuntimeError(f"{failure_stage} construction failed")

    if failure_stage == "observability":
        dependencies["observability_factory"] = lambda **_kwargs: (_ for _ in ()).throw(
            construction_error
        )
    elif failure_stage == "engine":
        dependencies["engine_factory"] = lambda _url: (_ for _ in ()).throw(
            construction_error
        )
    elif failure_stage == "session_factory":
        dependencies["session_factory_factory"] = lambda _engine: (
            _ for _ in ()
        ).throw(construction_error)
    elif failure_stage == "handlers":
        dependencies["outbox_handlers_factory"] = lambda: (_ for _ in ()).throw(
            construction_error
        )
    elif failure_stage == "observer":
        dependencies["observer_factory"] = lambda *_args, **_kwargs: (
            _ for _ in ()
        ).throw(construction_error)
    else:
        observer.start_error = construction_error

    if builder_name == "lifecycle":
        dependencies.pop("outbox_handlers_factory")
        builder = build_call_lifecycle_worker_runtime
    else:
        builder = build_background_worker_runtime

    with pytest.raises(RuntimeError) as caught:
        await builder(
            _test_settings(),
            arq_redis=redis,
            **dependencies,
        )

    assert caught.value is construction_error
    assert telemetry.close_calls == int(failure_stage != "observability")
    assert engine.close_calls == int(
        failure_stage not in {"observability", "engine"}
    )
    assert observer.close_calls == int(failure_stage == "observer_start")
    assert redis.aclose_calls == redis.close_calls == 0


@pytest.mark.anyio
async def test_validation_failure_precedes_every_background_factory() -> None:
    constructed: list[str] = []

    def forbidden(name: str):
        def factory(*_args: Any, **_kwargs: Any):
            constructed.append(name)
            raise AssertionError(f"{name} must not be constructed")

        return factory

    with pytest.raises(RuntimeError, match="LIVEKIT_AGENT_NAME"):
        await build_background_worker_runtime(
            _background_settings(livekit_agent_name=" "),
            arq_redis=_BorrowedRedis(),
            engine_factory=forbidden("engine"),
            session_factory_factory=forbidden("session_factory"),
            observability_factory=forbidden("observability"),
            observer_factory=forbidden("observer"),
            outbox_handlers_factory=forbidden("handlers"),
        )

    assert constructed == []


@pytest.mark.anyio
async def test_lifecycle_validation_failure_precedes_every_factory() -> None:
    constructed: list[str] = []

    def forbidden(name: str):
        def factory(*_args: Any, **_kwargs: Any):
            constructed.append(name)
            raise AssertionError(f"{name} must not be constructed")

        return factory

    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        await build_call_lifecycle_worker_runtime(
            _test_settings().model_copy(update={"database_url": ""}),
            arq_redis=_BorrowedRedis(),
            engine_factory=forbidden("engine"),
            session_factory_factory=forbidden("session_factory"),
            observability_factory=forbidden("observability"),
            observer_factory=forbidden("observer"),
        )

    assert constructed == []


@pytest.mark.anyio
async def test_partial_construction_preserves_primary_error_when_cleanup_fails_safely(
    caplog: pytest.LogCaptureFixture,
) -> None:
    events: list[str] = []
    close_error = RuntimeError("PRIVATE_CLEANUP_VALUE")
    construction_error = RuntimeError("construction failed")
    dependencies, _telemetry, engine, _observer, _handlers = _builder_dependencies(
        events
    )
    engine.close_error = close_error
    dependencies["session_factory_factory"] = lambda _engine: (
        _ for _ in ()
    ).throw(construction_error)

    with caplog.at_level(logging.WARNING), pytest.raises(RuntimeError) as caught:
        await build_background_worker_runtime(
            _test_settings(),
            arq_redis=_BorrowedRedis(),
            **dependencies,
        )

    assert caught.value is construction_error
    assert "event=worker_runtime_partial_cleanup_failed" in caplog.text
    assert "PRIVATE_CLEANUP_VALUE" not in caplog.text


@pytest.mark.anyio
async def test_partial_startup_construction_cancellation_closes_prior_resources(
    caplog: pytest.LogCaptureFixture,
) -> None:
    events: list[str] = []
    cancellation = asyncio.CancelledError("construction-cancelled")
    dependencies, telemetry, engine, _observer, _handlers = _builder_dependencies(
        events
    )
    dependencies["outbox_handlers_factory"] = lambda: (_ for _ in ()).throw(
        cancellation
    )

    with caplog.at_level(logging.WARNING), pytest.raises(
        asyncio.CancelledError
    ) as caught:
        await build_background_worker_runtime(
            _test_settings(),
            arq_redis=_BorrowedRedis(),
            **dependencies,
        )

    assert caught.value is cancellation
    assert events[-4:] == [
        "engine.close:start",
        "engine.close:end",
        "telemetry.close:start",
        "telemetry.close:end",
    ]
    assert engine.close_calls == telemetry.close_calls == 1
    assert "worker_runtime_partial_cleanup_failed" not in caplog.text


@pytest.mark.anyio
async def test_outer_cancellation_waits_for_blocked_partial_startup_cleanup(
    caplog: pytest.LogCaptureFixture,
) -> None:
    events: list[str] = []
    close_started = asyncio.Event()
    close_release = asyncio.Event()
    construction_error = RuntimeError("PRIVATE_CONSTRUCTION_VALUE")
    dependencies, telemetry, engine, _observer, _handlers = _builder_dependencies(
        events
    )
    engine.close_started = close_started
    engine.close_release = close_release
    dependencies["outbox_handlers_factory"] = lambda: (_ for _ in ()).throw(
        construction_error
    )

    task = asyncio.create_task(
        build_background_worker_runtime(
            _test_settings(),
            arq_redis=_BorrowedRedis(),
            **dependencies,
        )
    )
    await close_started.wait()
    with caplog.at_level(logging.WARNING):
        assert task.cancel("outer-startup-cancelled") is True
        await asyncio.sleep(0)
        assert not task.done()
        close_release.set()
        with pytest.raises(asyncio.CancelledError) as caught:
            await task

    assert caught.value.args == ("outer-startup-cancelled",)
    assert events[-4:] == [
        "engine.close:start",
        "engine.close:end",
        "telemetry.close:start",
        "telemetry.close:end",
    ]
    assert task.done()
    assert engine.close_calls == telemetry.close_calls == 1
    assert "PRIVATE_CONSTRUCTION_VALUE" not in caplog.text
    assert "worker_runtime_partial_cleanup_failed" not in caplog.text


@pytest.mark.anyio
async def test_partial_cleanup_cancellation_precedes_construction_error_by_identity(
    caplog: pytest.LogCaptureFixture,
) -> None:
    events: list[str] = []
    construction_error = RuntimeError("PRIVATE_CONSTRUCTION_VALUE")
    cleanup_cancellation = asyncio.CancelledError("cleanup-origin")
    dependencies, telemetry, engine, _observer, _handlers = _builder_dependencies(
        events
    )
    engine.close_error = cleanup_cancellation
    dependencies["outbox_handlers_factory"] = lambda: (_ for _ in ()).throw(
        construction_error
    )

    with caplog.at_level(logging.WARNING), pytest.raises(
        asyncio.CancelledError
    ) as caught:
        await build_background_worker_runtime(
            _test_settings(),
            arq_redis=_BorrowedRedis(),
            **dependencies,
        )

    assert caught.value is cleanup_cancellation
    assert events[-4:] == [
        "engine.close:start",
        "engine.close:end",
        "telemetry.close:start",
        "telemetry.close:end",
    ]
    assert engine.close_calls == telemetry.close_calls == 1
    assert "worker_runtime_partial_cleanup_failed" not in caplog.text
    rendered = "".join(traceback.format_exception(caught.value))
    assert "PRIVATE_CONSTRUCTION_VALUE" not in rendered
