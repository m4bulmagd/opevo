import pytest

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


@pytest.mark.anyio
@pytest.mark.parametrize("wake_fails", [False, True])
async def test_recovered_calls_wake_outbox_after_reconciliation_without_affecting_result(
    monkeypatch: pytest.MonkeyPatch,
    wake_fails: bool,
) -> None:
    class _Service:
        def __init__(self, _factory) -> None:
            pass

        async def reconcile(self, _now, *, limit: int):
            assert limit == 100
            return ReconciliationResult(scanned=1, recovered=1)

    monkeypatch.setattr(job_module, "CallReconciliationService", _Service)
    pool = _Pool(fail=wake_fails)

    result = await job_module.call_reconciliation_job(
        {
            "session_factory": object(),
            "arq_pool": pool,
        }
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
        def __init__(self, _factory) -> None:
            pass

        async def reconcile(self, _now, *, limit: int):
            assert limit == 100
            return result

    monkeypatch.setattr(job_module, "CallReconciliationService", _Service)
    pool = _Pool()

    await job_module.call_reconciliation_job(
        {
            "session_factory": object(),
            "arq_pool": pool,
        }
    )

    assert pool.jobs == [
        ("outbox_delivery_job", {}, {"_queue_name": "arq:queue:background"})
    ]


@pytest.mark.anyio
@pytest.mark.parametrize("snapshot_fails", [False, True])
async def test_call_reconciliation_snapshot_is_observed_without_changing_result(
    monkeypatch: pytest.MonkeyPatch,
    snapshot_fails: bool,
) -> None:
    """Optional worker telemetry must never change durable reconciliation outcomes."""
    result = ReconciliationResult(scanned=0, recovered=0, failed=0, deferred=0)
    snapshot = object()
    observed_results: list[dict[str, int]] = []
    observed_snapshots: list[object] = []

    class _Service:
        def __init__(self, _factory) -> None:
            pass

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

    class _Telemetry:
        def record_reconciliation_outcomes(self, value: dict[str, int]) -> None:
            observed_results.append(value)

        def record_call_snapshot(self, value: object) -> None:
            observed_snapshots.append(value)

    monkeypatch.setattr(job_module, "CallReconciliationService", _Service)
    monkeypatch.setattr(job_module, "CallRepository", _Repository)
    monkeypatch.setattr(job_module, "get_settings", object)

    response = await job_module.call_reconciliation_job(
        {
            "session_factory": _SessionContext,
            "observability": _Telemetry(),
        }
    )

    assert response == {
        "scanned": 0,
        "recovered": 0,
        "failed": 0,
        "deferred": 0,
    }
    assert observed_results == [response]
    assert observed_snapshots == ([] if snapshot_fails else [snapshot])
