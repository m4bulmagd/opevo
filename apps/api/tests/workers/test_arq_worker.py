import importlib
import logging
from typing import Any

import pytest
from arq.connections import RedisSettings
from arq.worker import Worker

from app.core import logging as app_logging
from app.core.config import Settings
from app.workers import arq_worker


def registered_names(functions: list[Any]) -> set[str]:
    return {function.name for function in functions}


def cron_names(cron_jobs: list[Any]) -> set[str]:
    return {job.name for job in cron_jobs}


def registrations_by_name(registrations: list[Any]) -> dict[str, Any]:
    return {registration.name: registration for registration in registrations}


def test_arq_worker_log_sanitizer_install_is_idempotent() -> None:
    sanitizer_type = getattr(app_logging, "ArqWorkerLogSanitizer", None)
    installer = getattr(app_logging, "install_arq_worker_log_sanitizer", None)
    assert sanitizer_type is not None
    assert installer is not None
    logger = logging.getLogger("arq.worker")

    installer()
    installer()

    assert sum(
        isinstance(item, sanitizer_type) for item in logger.filters
    ) == 1


def test_worker_registries_are_exact_and_disjoint() -> None:
    assert registered_names(arq_worker.CallLifecycleWorkerSettings.functions) == {
        "call_finalization_job",
        "call_reconciliation_job",
    }
    assert cron_names(arq_worker.CallLifecycleWorkerSettings.cron_jobs) == {
        "call_reconciliation_job"
    }
    assert registered_names(arq_worker.BackgroundWorkerSettings.functions) == {
        "outbox_delivery_job"
    }
    assert cron_names(arq_worker.BackgroundWorkerSettings.cron_jobs) == {
        "outbox_reconciliation_job",
        "verification_expiry_job",
    }
    assert not hasattr(arq_worker, "WorkerSettings")


def test_worker_settings_use_exact_runtime_limits() -> None:
    lifecycle = arq_worker.CallLifecycleWorkerSettings
    background = arq_worker.BackgroundWorkerSettings

    assert lifecycle.queue_name == "arq:queue"
    assert lifecycle.max_jobs == 10
    assert lifecycle.poll_delay == 0.5
    assert lifecycle.job_completion_wait == 60
    assert lifecycle.health_check_interval == 15
    assert lifecycle.health_check_key == "presvo:worker:call-lifecycle:health"

    assert background.queue_name == "arq:queue:background"
    assert background.max_jobs == 4
    assert background.poll_delay == 0.5
    assert background.job_completion_wait == 30
    assert background.health_check_interval == 15
    assert background.health_check_key == "presvo:worker:background:health"


def test_enqueued_function_policies_and_result_retention_are_explicit() -> None:
    lifecycle = registrations_by_name(arq_worker.CallLifecycleWorkerSettings.functions)
    background = registrations_by_name(arq_worker.BackgroundWorkerSettings.functions)

    assert (
        lifecycle["call_finalization_job"].timeout_s,
        lifecycle["call_finalization_job"].max_tries,
    ) == (
        35,
        3,
    )
    assert (
        lifecycle["call_reconciliation_job"].timeout_s,
        lifecycle["call_reconciliation_job"].max_tries,
    ) == (
        65,
        1,
    )
    assert (
        background["outbox_delivery_job"].timeout_s,
        background["outbox_delivery_job"].max_tries,
    ) == (
        305,
        1,
    )
    assert lifecycle["call_finalization_job"].keep_result_s is None
    assert lifecycle["call_reconciliation_job"].keep_result_s == 0
    assert background["outbox_delivery_job"].keep_result_s is None


def test_cron_policies_schedule_and_zero_result_retention_are_explicit() -> None:
    lifecycle = registrations_by_name(arq_worker.CallLifecycleWorkerSettings.cron_jobs)
    background = registrations_by_name(arq_worker.BackgroundWorkerSettings.cron_jobs)

    assert (
        lifecycle["call_reconciliation_job"].timeout_s,
        lifecycle["call_reconciliation_job"].max_tries,
    ) == (
        65,
        1,
    )
    assert (
        background["outbox_reconciliation_job"].timeout_s,
        background["outbox_reconciliation_job"].max_tries,
    ) == (
        305,
        1,
    )
    assert (
        background["verification_expiry_job"].timeout_s,
        background["verification_expiry_job"].max_tries,
    ) == (
        65,
        1,
    )
    assert all(
        registration.minute == set(range(60))
        for registration in [*lifecycle.values(), *background.values()]
    )
    assert all(
        registration.keep_result_s == 0
        for registration in [*lifecycle.values(), *background.values()]
    )


def test_cron_construction_supplies_explicit_zero_result_retention(
    monkeypatch,
) -> None:
    cron_module = importlib.import_module("arq.cron")
    real_cron = cron_module.cron
    constructed: list[tuple[str | None, dict[str, Any]]] = []

    def capture_cron(*args, **kwargs):
        constructed.append((kwargs.get("name"), dict(kwargs)))
        return real_cron(*args, **kwargs)

    monkeypatch.setattr(cron_module, "cron", capture_cron)
    try:
        importlib.reload(arq_worker)
    finally:
        monkeypatch.setattr(cron_module, "cron", real_cron)
        importlib.reload(arq_worker)

    by_name = {name: kwargs for name, kwargs in constructed}
    assert set(by_name) == {
        "call_reconciliation_job",
        "outbox_reconciliation_job",
        "verification_expiry_job",
    }
    assert all(
        "keep_result" in kwargs and kwargs["keep_result"] == 0
        for kwargs in by_name.values()
    )


@pytest.mark.anyio
async def test_effective_lifecycle_registry_uses_one_reconciliation_contract() -> None:
    worker = Worker(
        functions=arq_worker.CallLifecycleWorkerSettings.functions,
        cron_jobs=arq_worker.CallLifecycleWorkerSettings.cron_jobs,
        handle_signals=False,
    )

    assert set(worker.functions) == {
        "call_finalization_job",
        "call_reconciliation_job",
    }
    assert worker.functions["call_finalization_job"].keep_result_s is None
    assert worker.functions["call_reconciliation_job"].keep_result_s == 0


def test_both_worker_classes_share_one_captured_boundary_settings_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core import config as config_module

    configured = Settings(
        _env_file=None,
        app_env="test",
        database_url="sqlite+aiosqlite://",
        redis_url="redis://redis:6379/7",
        worker_lifecycle_max_jobs=17,
        worker_background_max_jobs=9,
    )
    calls: list[None] = []
    original_get_settings = config_module.get_settings

    def capture_settings() -> Settings:
        calls.append(None)
        return configured

    monkeypatch.setattr(config_module, "get_settings", capture_settings)
    try:
        module = importlib.reload(arq_worker)
        assert calls == [None]
        assert module._WORKER_SETTINGS is configured
        assert module.CallLifecycleWorkerSettings.max_jobs == 17
        assert module.BackgroundWorkerSettings.max_jobs == 9
        for worker_settings in (
            module.CallLifecycleWorkerSettings,
            module.BackgroundWorkerSettings,
        ):
            assert isinstance(worker_settings.redis_settings, RedisSettings)
            assert worker_settings.redis_settings.host == "redis"
            assert worker_settings.redis_settings.port == 6379
            assert worker_settings.redis_settings.database == 7
    finally:
        monkeypatch.setattr(config_module, "get_settings", original_get_settings)
        with monkeypatch.context() as environment:
            environment.setenv("APP_ENV", "test")
            environment.setenv("DATABASE_URL", "sqlite+aiosqlite://")
            environment.setenv("REDIS_URL", "redis://localhost:6379/0")
            importlib.reload(arq_worker)


class _Redis:
    def __init__(self) -> None:
        self.close_calls = 0
        self.aclose_calls = 0

    async def close(self) -> None:
        self.close_calls += 1

    async def aclose(self) -> None:
        self.aclose_calls += 1


class _Cleanup:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.close_calls = 0

    async def aclose(self) -> None:
        self.close_calls += 1
        self.events.append("runtime.close")


def _worker_runtime(runtime_type, events: list[str]):
    from app.composition.runtime import (
        BackgroundWorkerRuntime,
        CallLifecycleWorkerRuntime,
    )

    values = {
        "settings": arq_worker._WORKER_SETTINGS,
        "session_factory": object(),
        "arq_pool": object(),
        "observability": object(),
        "queue_observer": object(),
        "now": lambda: None,
        "_cleanup": _Cleanup(events),
    }
    if runtime_type is BackgroundWorkerRuntime:
        return BackgroundWorkerRuntime(outbox_handlers={}, **values)
    return CallLifecycleWorkerRuntime(**values)


def _patch_startup_builder(monkeypatch, *, background: bool):
    from app.composition.runtime import (
        BackgroundWorkerRuntime,
        CallLifecycleWorkerRuntime,
    )

    events: list[str] = []
    runtime_type = BackgroundWorkerRuntime if background else CallLifecycleWorkerRuntime
    runtime = _worker_runtime(runtime_type, events)
    captured: dict[str, object] = {}

    monkeypatch.setattr(arq_worker, "setup_logging", lambda: events.append("logging"))
    monkeypatch.setattr(
        arq_worker,
        "install_arq_worker_log_sanitizer",
        lambda: events.append("arq.logging"),
    )

    async def build(settings, *, arq_redis):
        captured.update(settings=settings, arq_redis=arq_redis)
        events.append("runtime.build")
        return runtime

    builder_name = (
        "build_background_worker_runtime"
        if background
        else "build_call_lifecycle_worker_runtime"
    )
    monkeypatch.setattr(arq_worker, builder_name, build)
    return events, runtime, captured


@pytest.mark.anyio
async def test_call_lifecycle_startup_uses_only_arq_owned_resources(
    monkeypatch,
) -> None:
    from app.composition.runtime import WORKER_RUNTIME_KEY

    redis = _Redis()
    events, runtime, captured = _patch_startup_builder(monkeypatch, background=False)
    enqueue_time = object()
    ctx = {"redis": redis, "job_try": 2, "enqueue_time": enqueue_time}

    await arq_worker.on_call_lifecycle_startup(ctx)

    assert events == ["logging", "arq.logging", "runtime.build"]
    assert ctx == {
        "redis": redis,
        "job_try": 2,
        "enqueue_time": enqueue_time,
        WORKER_RUNTIME_KEY: runtime,
    }
    assert captured == {
        "settings": arq_worker._WORKER_SETTINGS,
        "arq_redis": redis,
    }


@pytest.mark.anyio
async def test_background_startup_stores_only_the_typed_application_runtime(
    monkeypatch,
) -> None:
    from app.composition.runtime import WORKER_RUNTIME_KEY

    redis = _Redis()
    events, runtime, captured = _patch_startup_builder(monkeypatch, background=True)
    ctx = {"redis": redis}

    await arq_worker.on_background_startup(ctx)

    assert events == ["logging", "arq.logging", "runtime.build"]
    assert ctx == {"redis": redis, WORKER_RUNTIME_KEY: runtime}
    assert captured == {
        "settings": arq_worker._WORKER_SETTINGS,
        "arq_redis": redis,
    }


@pytest.mark.anyio
async def test_shutdown_pops_and_closes_the_typed_runtime_once() -> None:
    from app.composition.runtime import WORKER_RUNTIME_KEY, CallLifecycleWorkerRuntime

    events: list[str] = []
    redis = _Redis()
    runtime = _worker_runtime(CallLifecycleWorkerRuntime, events)
    ctx = {"redis": redis, WORKER_RUNTIME_KEY: runtime}

    await arq_worker.on_shutdown(ctx)
    await arq_worker.on_shutdown(ctx)

    assert events == ["runtime.close"]
    assert runtime._cleanup.close_calls == 1
    assert ctx == {"redis": redis}
    assert redis.close_calls == 0
    assert redis.aclose_calls == 0


@pytest.mark.anyio
async def test_shutdown_rejects_and_removes_an_invalid_runtime_type() -> None:
    from app.composition.runtime import (
        WORKER_RUNTIME_KEY,
        WorkerRuntimeConfigurationError,
    )

    ctx = {"redis": object(), WORKER_RUNTIME_KEY: object()}

    with pytest.raises(WorkerRuntimeConfigurationError, match="invalid type"):
        await arq_worker.on_shutdown(ctx)

    assert ctx == {"redis": ctx["redis"]}


@pytest.mark.anyio
async def test_worker_startup_rejects_unsafe_runtime_before_resources(
    monkeypatch,
) -> None:
    from app.composition.runtime import WORKER_RUNTIME_KEY

    events: list[str] = []
    monkeypatch.setattr(arq_worker, "setup_logging", lambda: events.append("logging"))
    monkeypatch.setattr(
        arq_worker,
        "install_arq_worker_log_sanitizer",
        lambda: events.append("arq.logging"),
    )
    async def reject_runtime(settings, *, arq_redis) -> None:
        assert settings is arq_worker._WORKER_SETTINGS
        assert arq_redis is ctx["redis"]
        events.append("runtime.build")
        raise RuntimeError("TELNYX_ORDERING_ENABLED")

    monkeypatch.setattr(arq_worker, "build_background_worker_runtime", reject_runtime)
    ctx = {"redis": object()}

    with pytest.raises(RuntimeError, match="TELNYX_ORDERING_ENABLED"):
        await arq_worker.on_background_startup(ctx)

    assert events == ["logging", "arq.logging", "runtime.build"]
    assert WORKER_RUNTIME_KEY not in ctx


@pytest.mark.parametrize(
    "service_name",
    ["presvo-worker-call-lifecycle", "presvo-worker-background"],
)
def test_worker_service_names_are_preserved_by_observability_allowlist(
    service_name: str,
) -> None:
    from app.core.observability import (
        initialize_observability,
        reset_observability_for_tests,
    )

    observed: list[str] = []

    def factory(actual_service_name, _trace_endpoint, _metric_endpoint):
        from opentelemetry import metrics, trace

        observed.append(actual_service_name)
        return metrics.get_meter("test"), trace.get_tracer("test"), None

    reset_observability_for_tests()
    try:
        initialize_observability(
            service_name=service_name,
            endpoint="https://otel.example",
            components_factory=factory,
        )
    finally:
        reset_observability_for_tests()

    assert observed == [service_name]
