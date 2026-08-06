from contextlib import AsyncExitStack, asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.composition.lifecycle import RuntimeCleanup
from app.composition.runtime import (
    WORKER_RUNTIME_KEY,
    BackgroundWorkerRuntime,
    CallLifecycleWorkerRuntime,
    WorkerRuntimeConfigurationError,
)
from app.core.config import Settings
from app.workers.jobs import call_finalization as worker_module


class _SessionFactory:
    @asynccontextmanager
    async def __call__(self):
        yield object()


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        database_url="sqlite+aiosqlite://",
        redis_url="redis://worker.invalid/0",
    )


def _lifecycle_runtime(
    session_factory: object,
) -> CallLifecycleWorkerRuntime:
    return CallLifecycleWorkerRuntime(
        settings=_settings(),
        session_factory=session_factory,
        arq_pool=object(),
        observability=object(),
        queue_observer=object(),
        now=lambda: datetime(2026, 8, 6, tzinfo=UTC),
        _cleanup=RuntimeCleanup(AsyncExitStack()),
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

    result = await worker_module.finalize_call(
        {"call_id": str(call_id)},
        session_factory=_SessionFactory(),
    )

    assert result == {"status": "stale", "minutes_charged": 0}


@pytest.mark.anyio
async def test_call_finalization_wrapper_passes_lifecycle_session_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_factory = _SessionFactory()
    payload = {"call_id": str(uuid4())}
    captured: list[tuple[dict, object]] = []

    async def capture(
        captured_payload: dict,
        *,
        session_factory: object,
    ) -> dict:
        captured.append((captured_payload, session_factory))
        return {"status": "captured"}

    monkeypatch.setattr(worker_module, "finalize_call", capture)

    result = await worker_module.call_finalization_job(
        {WORKER_RUNTIME_KEY: _lifecycle_runtime(session_factory)},
        payload,
    )

    assert result == {"status": "captured"}
    assert captured == [(payload, session_factory)]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "ctx",
    [{}, {WORKER_RUNTIME_KEY: _background_runtime()}],
)
async def test_call_finalization_wrapper_rejects_invalid_runtime(
    ctx: dict,
) -> None:
    with pytest.raises(
        WorkerRuntimeConfigurationError,
        match="call-lifecycle",
    ):
        await worker_module.call_finalization_job(
            ctx,
            {"call_id": str(uuid4())},
        )
