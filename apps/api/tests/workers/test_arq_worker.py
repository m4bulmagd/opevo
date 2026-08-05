import asyncio
import importlib
from types import SimpleNamespace
from typing import Any

import pytest
from arq.connections import RedisSettings

from app.core.config import get_settings
from app.workers import arq_worker


def registered_names(functions: list[Any]) -> set[str]:
    return {function.name for function in functions}


def cron_names(cron_jobs: list[Any]) -> set[str]:
    return {job.name for job in cron_jobs}


def registrations_by_name(registrations: list[Any]) -> dict[str, Any]:
    return {registration.name: registration for registration in registrations}


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
    assert all(
        registration.keep_result_s is None
        for registration in [*lifecycle.values(), *background.values()]
    )


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


def test_worker_settings_use_configured_redis_url(monkeypatch) -> None:
    with monkeypatch.context() as environment:
        environment.setenv("REDIS_URL", "redis://redis:6379/7")
        get_settings.cache_clear()
        module = importlib.reload(arq_worker)

        for worker_settings in (
            module.CallLifecycleWorkerSettings,
            module.BackgroundWorkerSettings,
        ):
            assert isinstance(worker_settings.redis_settings, RedisSettings)
            assert worker_settings.redis_settings.host == "redis"
            assert worker_settings.redis_settings.port == 6379
            assert worker_settings.redis_settings.database == 7

    get_settings.cache_clear()
    importlib.reload(arq_worker)


def test_worker_concurrency_defaults_are_consumed_by_the_correct_class(
    monkeypatch,
) -> None:
    with monkeypatch.context() as environment:
        environment.delenv("WORKER_LIFECYCLE_MAX_JOBS", raising=False)
        environment.delenv("WORKER_BACKGROUND_MAX_JOBS", raising=False)
        get_settings.cache_clear()
        module = importlib.reload(arq_worker)

        assert module.CallLifecycleWorkerSettings.max_jobs == 10
        assert module.BackgroundWorkerSettings.max_jobs == 4

    get_settings.cache_clear()
    importlib.reload(arq_worker)


def test_worker_concurrency_overrides_are_consumed_by_the_correct_class(
    monkeypatch,
) -> None:
    with monkeypatch.context() as environment:
        environment.setenv("WORKER_LIFECYCLE_MAX_JOBS", "17")
        environment.setenv("WORKER_BACKGROUND_MAX_JOBS", "9")
        get_settings.cache_clear()
        module = importlib.reload(arq_worker)

        assert module.CallLifecycleWorkerSettings.max_jobs == 17
        assert module.BackgroundWorkerSettings.max_jobs == 9

    get_settings.cache_clear()
    importlib.reload(arq_worker)


class _Redis:
    def __init__(self) -> None:
        self.close_calls = 0
        self.aclose_calls = 0

    async def close(self) -> None:
        self.close_calls += 1

    async def aclose(self) -> None:
        self.aclose_calls += 1


class _Observer:
    def __init__(self, events: list[str]) -> None:
        self._events = events
        self.start_calls = 0
        self.close_calls = 0

    def start(self) -> None:
        self.start_calls += 1
        self._events.append("observer.start")

    async def aclose(self) -> None:
        self.close_calls += 1
        self._events.append("observer.close")


def _patch_startup_dependencies(monkeypatch, *, expected_service_name: str):
    events: list[str] = []
    settings = SimpleNamespace(otel_exporter_otlp_endpoint="https://otel.example")
    telemetry = object()
    handlers = {"topic": object()}
    observer = _Observer(events)
    observer_arguments: dict[str, Any] = {}

    monkeypatch.setattr(arq_worker, "setup_logging", lambda: events.append("logging"))
    monkeypatch.setattr(arq_worker, "get_settings", lambda: settings)

    def validate(actual_settings) -> None:
        assert actual_settings is settings
        events.append("validation")

    monkeypatch.setattr(arq_worker, "validate_worker_runtime", validate)

    def initialize(*, service_name: str, endpoint: str | None):
        assert service_name == expected_service_name
        assert endpoint == "https://otel.example"
        events.append("telemetry")
        return telemetry

    monkeypatch.setattr(arq_worker, "initialize_observability", initialize)

    def get_handlers():
        events.append("handlers")
        return handlers

    monkeypatch.setattr(arq_worker, "get_default_outbox_handlers", get_handlers)

    def construct_observer(redis, actual_telemetry, **kwargs):
        observer_arguments.update(
            redis=redis,
            telemetry=actual_telemetry,
            **kwargs,
        )
        events.append("observer")
        return observer

    monkeypatch.setattr(arq_worker, "QueueObserver", construct_observer)

    async def reject_second_pool(*_args, **_kwargs):
        raise AssertionError("startup must use ARQ's existing Redis pool")

    monkeypatch.setattr(arq_worker, "create_pool", reject_second_pool, raising=False)
    return events, telemetry, handlers, observer, observer_arguments


@pytest.mark.anyio
async def test_call_lifecycle_startup_uses_only_arq_owned_resources(
    monkeypatch,
) -> None:
    redis = _Redis()
    events, telemetry, _handlers, observer, observer_arguments = (
        _patch_startup_dependencies(
            monkeypatch,
            expected_service_name="presvo-worker-call-lifecycle",
        )
    )
    ctx = {"redis": redis}

    await arq_worker.on_call_lifecycle_startup(ctx)

    assert events == [
        "logging",
        "validation",
        "telemetry",
        "observer",
        "observer.start",
    ]
    assert ctx["arq_pool"] is redis
    assert ctx["observability"] is telemetry
    assert ctx["queue_observer"] is observer
    assert "outbox_handlers" not in ctx
    assert observer_arguments == {
        "redis": redis,
        "telemetry": telemetry,
        "queue_name": "arq:queue",
        "queue_class": "call_lifecycle",
    }


@pytest.mark.anyio
async def test_background_startup_constructs_handlers_after_telemetry(
    monkeypatch,
) -> None:
    redis = _Redis()
    events, telemetry, handlers, observer, observer_arguments = (
        _patch_startup_dependencies(
            monkeypatch,
            expected_service_name="presvo-worker-background",
        )
    )
    ctx = {"redis": redis}

    await arq_worker.on_background_startup(ctx)

    assert events == [
        "logging",
        "validation",
        "telemetry",
        "handlers",
        "observer",
        "observer.start",
    ]
    assert ctx["arq_pool"] is redis
    assert ctx["observability"] is telemetry
    assert ctx["outbox_handlers"] is handlers
    assert ctx["queue_observer"] is observer
    assert observer_arguments == {
        "redis": redis,
        "telemetry": telemetry,
        "queue_name": "arq:queue:background",
        "queue_class": "background",
    }


@pytest.mark.anyio
async def test_shutdown_closes_owned_resources_once_in_order(monkeypatch) -> None:
    events: list[str] = []
    redis = _Redis()
    observer = _Observer(events)
    telemetry = object()

    async def shutdown(actual_telemetry) -> None:
        assert actual_telemetry is telemetry
        events.append("telemetry.shutdown")

    monkeypatch.setattr(arq_worker, "shutdown_observability", shutdown)
    ctx = {
        "redis": redis,
        "arq_pool": redis,
        "queue_observer": observer,
        "observability": telemetry,
    }

    await arq_worker.on_shutdown(ctx)
    await arq_worker.on_shutdown(ctx)

    assert events == ["observer.close", "telemetry.shutdown"]
    assert observer.close_calls == 1
    assert redis.close_calls == 0
    assert redis.aclose_calls == 0


@pytest.mark.anyio
@pytest.mark.parametrize(
    "error", [asyncio.CancelledError(), RuntimeError("observer failed")]
)
async def test_shutdown_propagates_observer_failure_after_telemetry_cleanup(
    monkeypatch,
    error: BaseException,
) -> None:
    events: list[str] = []
    telemetry = object()

    class FailingObserver:
        async def aclose(self) -> None:
            events.append("observer.close")
            raise error

    async def shutdown(actual_telemetry) -> None:
        assert actual_telemetry is telemetry
        events.append("telemetry.shutdown")

    monkeypatch.setattr(arq_worker, "shutdown_observability", shutdown)
    ctx = {
        "queue_observer": FailingObserver(),
        "observability": telemetry,
    }

    with pytest.raises(type(error)) as captured:
        await arq_worker.on_shutdown(ctx)

    assert captured.value is error
    assert events == ["observer.close", "telemetry.shutdown"]
    await arq_worker.on_shutdown(ctx)
    assert events == ["observer.close", "telemetry.shutdown"]


@pytest.mark.anyio
async def test_worker_startup_rejects_unsafe_runtime_before_resources(
    monkeypatch,
) -> None:
    events: list[str] = []
    settings = SimpleNamespace(otel_exporter_otlp_endpoint=None)
    monkeypatch.setattr(arq_worker, "setup_logging", lambda: events.append("logging"))
    monkeypatch.setattr(arq_worker, "get_settings", lambda: settings)

    def reject_runtime(actual_settings) -> None:
        assert actual_settings is settings
        events.append("validation")
        raise RuntimeError("TELNYX_ORDERING_ENABLED")

    monkeypatch.setattr(arq_worker, "validate_worker_runtime", reject_runtime)
    ctx = {"redis": object()}

    with pytest.raises(RuntimeError, match="TELNYX_ORDERING_ENABLED"):
        await arq_worker.on_background_startup(ctx)

    assert events == ["logging", "validation"]
    assert "observability" not in ctx
    assert "outbox_handlers" not in ctx
    assert "queue_observer" not in ctx


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
