import pytest

from app.services.call_reconciliation_service import ReconciliationResult
from app.workers.jobs import call_reconciliation as job_module


class _Pool:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.jobs: list[tuple[str, dict]] = []

    async def enqueue_job(self, name: str, payload: dict) -> None:
        self.jobs.append((name, payload))
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
    assert pool.jobs == [("outbox_delivery_job", {})]


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

    assert pool.jobs == [("outbox_delivery_job", {})]
