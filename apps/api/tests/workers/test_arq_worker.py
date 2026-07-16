import importlib
import inspect
from types import SimpleNamespace

import pytest
from arq.connections import RedisSettings

from app.core.config import get_settings
from app.workers import arq_worker


def test_worker_settings_use_configured_redis_url(monkeypatch) -> None:
    monkeypatch.setenv("REDIS_URL", "redis://redis:6379/7")
    get_settings.cache_clear()

    module = importlib.reload(arq_worker)

    assert isinstance(module.WorkerSettings.redis_settings, RedisSettings)
    assert module.WorkerSettings.redis_settings.host == "redis"
    assert module.WorkerSettings.redis_settings.port == 6379
    assert module.WorkerSettings.redis_settings.database == 7

    get_settings.cache_clear()


def test_worker_job_functions_accept_arq_context() -> None:
    for function in arq_worker.WorkerSettings.functions:
        parameters = list(inspect.signature(function).parameters.values())
        assert parameters
        assert parameters[0].name == "ctx"


def test_worker_registers_outbox_wakeup_and_reconciliation() -> None:
    from app.services.outbox_service import SUPPORTED_OUTBOX_TOPICS
    from app.workers.jobs.outbox_topics import DEFAULT_OUTBOX_HANDLERS

    function_names = {
        function.__name__ for function in arq_worker.WorkerSettings.functions
    }

    assert "outbox_delivery_job" in function_names
    assert "summary_job" not in function_names
    assert "recording_job" not in function_names
    assert "notifications_job" not in function_names
    assert "call_finalization_job" in function_names
    assert "call_reconciliation_job" in function_names
    assert any(
        getattr(job, "name", None) == "outbox_reconciliation_job"
        for job in arq_worker.WorkerSettings.cron_jobs
    )
    assert set(DEFAULT_OUTBOX_HANDLERS) == set(SUPPORTED_OUTBOX_TOPICS)


@pytest.mark.anyio
async def test_worker_startup_initializes_safe_logging(monkeypatch) -> None:
    setup_calls: list[bool] = []
    monkeypatch.setattr(arq_worker, "setup_logging", lambda: setup_calls.append(True), raising=False)

    await arq_worker.WorkerSettings.on_startup({})

    assert setup_calls == [True]


@pytest.mark.anyio
async def test_worker_startup_rejects_unsafe_runtime_before_jobs(
    monkeypatch,
) -> None:
    settings = SimpleNamespace(otel_exporter_otlp_endpoint=None)
    monkeypatch.setattr(arq_worker, "get_settings", lambda: settings)

    def reject_runtime(actual_settings) -> None:
        assert actual_settings is settings
        raise RuntimeError("TELNYX_ORDERING_ENABLED")

    monkeypatch.setattr(arq_worker, "validate_api_runtime", reject_runtime)

    with pytest.raises(RuntimeError, match="TELNYX_ORDERING_ENABLED"):
        await arq_worker.WorkerSettings.on_startup({})
