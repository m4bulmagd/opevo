from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.workers.jobs import call_finalization as worker_module


class _SessionFactory:
    @asynccontextmanager
    async def __call__(self):
        yield object()


@pytest.mark.anyio
async def test_worker_reports_stale_generation_without_claiming_completion(
    monkeypatch,
) -> None:
    call_id = uuid4()

    class FakeLifecycle:
        def __init__(self, _session) -> None:
            pass

        async def claim_finalization(self, claimed_call_id):
            assert claimed_call_id == call_id
            return SimpleNamespace(
                unavailable=False,
                already_completed=False,
                generation=4,
            )

        async def complete_finalization(self, completed_call_id, *, generation):
            assert completed_call_id == call_id
            assert generation == 4
            return SimpleNamespace(
                stale_generation=True,
                already_completed=False,
                minutes_charged=0,
            )

    monkeypatch.setattr(worker_module, "CallLifecycleService", FakeLifecycle)

    result = await worker_module.call_finalization_job(
        {"session_factory": _SessionFactory()},
        {"call_id": str(call_id)},
    )

    assert result == {"status": "stale", "minutes_charged": 0}
