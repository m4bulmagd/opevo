import logging
from contextlib import AsyncExitStack
from datetime import UTC, datetime

import pytest

from app.composition.lifecycle import RuntimeCleanup
from app.composition.runtime import (
    WORKER_RUNTIME_KEY,
    BackgroundWorkerRuntime,
    CallLifecycleWorkerRuntime,
    WorkerRuntimeConfigurationError,
)
from app.core.config import Settings
from app.services.call_reconciliation_service import ReconciliationResult
from app.workers.jobs import call_reconciliation as job_module


class _Pool:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.jobs: list[tuple[str, dict, dict]] = []

    async def enqueue_job(self, name: str, payload: dict, **kwargs) -> None:
        self.jobs.append((name, payload, kwargs))
        if self.fail:
            raise RuntimeError("redis unavailable")


class _Telemetry:
    def record_reconciliation_outcomes(self, _value: dict[str, int]) -> None:
        pass


def _runtime(
    pool: object,
    telemetry: object | None = None,
    *,
    session_factory: object = object(),
    settings: Settings | None = None,
    now: object | None = None,
) -> CallLifecycleWorkerRuntime:
    return CallLifecycleWorkerRuntime(
        settings=settings or _settings(),
        session_factory=session_factory,
        arq_pool=pool,
        observability=telemetry or _Telemetry(),
        queue_observer=object(),
        now=now or (lambda: datetime(2026, 8, 6, tzinfo=UTC)),
        _cleanup=RuntimeCleanup(AsyncExitStack()),
    )


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        database_url="sqlite+aiosqlite://",
        redis_url="redis://worker.invalid/0",
    )


def _background_runtime() -> BackgroundWorkerRuntime:
    return BackgroundWorkerRuntime(
        settings=_settings(),
        session_factory=object(),
        arq_pool=object(),
        observability=object(),
        queue_observer=object(),
        outbox_handlers={},
        now=lambda: datetime(2026, 8, 6, tzinfo=UTC),
        _cleanup=RuntimeCleanup(AsyncExitStack()),
    )


@pytest.mark.anyio
@pytest.mark.parametrize("wake_fails", [False, True])
async def test_recovered_calls_wake_outbox_after_reconciliation_without_affecting_result(
    monkeypatch: pytest.MonkeyPatch,
    wake_fails: bool,
) -> None:
    class _Service:
        def __init__(self, _factory, *, settings: Settings) -> None:
            assert settings.call_reconciliation_pending_stale_seconds == 17

        async def reconcile(self, _now, *, limit: int):
            assert limit == 100
            return ReconciliationResult(scanned=1, recovered=1)

    explicit_settings = Settings(
        database_url="sqlite+aiosqlite://",
        redis_url="redis://explicit.invalid/0",
        call_reconciliation_pending_stale_seconds=17,
    )
    monkeypatch.setattr(job_module, "CallReconciliationService", _Service)
    pool = _Pool(fail=wake_fails)

    result = await job_module.reconcile_calls(
        session_factory=object(),
        arq_pool=pool,
        observability=_Telemetry(),
        settings=explicit_settings,
        now=lambda: datetime(2026, 8, 6, tzinfo=UTC),
    )

    assert result == {
        "scanned": 1,
        "recovered": 1,
        "failed": 0,
        "deferred": 0,
    }
    assert pool.jobs == [
        ("outbox_delivery_job", {}, {"_queue_name": "arq:queue:background"})
    ]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "result",
    [
        ReconciliationResult(scanned=1, failed=1),
        ReconciliationResult(scanned=1, deferred=1),
    ],
)
async def test_any_scanned_stale_work_wakes_possible_recording_intent(
    monkeypatch: pytest.MonkeyPatch,
    result: ReconciliationResult,
) -> None:
    class _Service:
        def __init__(self, _factory, *, settings: Settings) -> None:
            assert isinstance(settings, Settings)

        async def reconcile(self, _now, *, limit: int):
            assert limit == 100
            return result

    monkeypatch.setattr(job_module, "CallReconciliationService", _Service)
    pool = _Pool()

    await job_module.reconcile_calls(
        session_factory=object(),
        arq_pool=pool,
        observability=_Telemetry(),
        settings=_settings(),
        now=lambda: datetime(2026, 8, 6, tzinfo=UTC),
    )

    assert pool.jobs == [
        ("outbox_delivery_job", {}, {"_queue_name": "arq:queue:background"})
    ]


@pytest.mark.anyio
@pytest.mark.parametrize("snapshot_fails", [False, True])
async def test_call_reconciliation_snapshot_is_observed_without_changing_result(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    snapshot_fails: bool,
) -> None:
    """Optional worker telemetry must never change durable reconciliation outcomes."""
    result = ReconciliationResult(scanned=0, recovered=0, failed=0, deferred=0)
    snapshot = object()
    observed_snapshots: list[object] = []

    class _Service:
        def __init__(self, _factory, *, settings: Settings) -> None:
            assert isinstance(settings, Settings)

        async def reconcile(self, _now, *, limit: int):
            assert limit == 100
            return result

    class _SessionContext:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, *_args) -> None:
            return None

    class _Repository:
        def __init__(self, _session: object) -> None:
            pass

        async def observability_snapshot(self, _now, _settings) -> object:
            if snapshot_fails:
                raise RuntimeError("PRIVATE_SNAPSHOT_DETAIL")
            return snapshot

    class _SnapshotTelemetry:
        def record_reconciliation_outcomes(self, _value: dict[str, int]) -> None:
            pass

        def record_call_snapshot(self, value: object) -> None:
            observed_snapshots.append(value)

    monkeypatch.setattr(job_module, "CallReconciliationService", _Service)
    monkeypatch.setattr(job_module, "CallRepository", _Repository)
    explicit_settings = Settings(
        database_url="sqlite+aiosqlite://",
        redis_url="redis://explicit.invalid/0",
    )

    with caplog.at_level(logging.WARNING, logger=job_module.logger.name):
        telemetry = _SnapshotTelemetry()
        response = await job_module.reconcile_calls(
            session_factory=_SessionContext,
            arq_pool=object(),
            observability=telemetry,
            settings=explicit_settings,
            now=lambda: datetime(2026, 8, 6, tzinfo=UTC),
        )

    assert response == {
        "scanned": 0,
        "recovered": 0,
        "failed": 0,
        "deferred": 0,
    }
    assert observed_snapshots == ([] if snapshot_fails else [snapshot])
    expected_logs = (
        [
            (
                job_module.logger.name,
                logging.WARNING,
                "event=observability_snapshot_failed "
                "operation=collect_call_snapshot error_type=RuntimeError status=failed",
            )
        ]
        if snapshot_fails
        else []
    )
    assert caplog.record_tuples == expected_logs
    assert "PRIVATE_SNAPSHOT_DETAIL" not in caplog.text


@pytest.mark.anyio
async def test_call_reconciliation_wrapper_passes_lifecycle_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_factory = object()
    pool = object()
    telemetry = _Telemetry()
    settings = _settings()

    def now() -> datetime:
        return datetime(2026, 8, 6, tzinfo=UTC)

    captured: list[dict[str, object]] = []

    async def capture(**dependencies: object) -> dict[str, int]:
        captured.append(dependencies)
        return {"scanned": 0, "recovered": 0, "failed": 0, "deferred": 0}

    monkeypatch.setattr(job_module, "reconcile_calls", capture)
    runtime = _runtime(
        pool,
        telemetry,
        session_factory=session_factory,
        settings=settings,
        now=now,
    )

    result = await job_module.call_reconciliation_job(
        {WORKER_RUNTIME_KEY: runtime}
    )

    assert result == {
        "scanned": 0,
        "recovered": 0,
        "failed": 0,
        "deferred": 0,
    }
    assert captured == [
        {
            "session_factory": session_factory,
            "arq_pool": pool,
            "observability": telemetry,
            "settings": settings,
            "now": now,
        }
    ]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "ctx",
    [{}, {WORKER_RUNTIME_KEY: _background_runtime()}],
)
async def test_call_reconciliation_wrapper_rejects_invalid_runtime(
    ctx: dict,
) -> None:
    with pytest.raises(
        WorkerRuntimeConfigurationError,
        match="call-lifecycle",
    ):
        await job_module.call_reconciliation_job(ctx)
