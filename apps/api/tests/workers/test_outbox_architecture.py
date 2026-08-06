import inspect
from pathlib import Path
import subprocess
import sys
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.outbox_event import OutboxEvent
from app.services.outbox_service import (
    REFERENCE_PAYLOAD_FIELDS,
    OutboxService,
    SUPPORTED_OUTBOX_TOPICS,
)
from app.workers.outbox import delivery
from app.core.dispatch_token import DispatchTokenConfig
from app.workers.outbox.registry import build_outbox_handlers


API_ROOT = Path(__file__).resolve().parents[2]


def test_delivery_import_does_not_eagerly_import_topic_providers() -> None:
    script = "\n".join(
        (
            "import sys",
            "import app.workers.outbox.delivery",
            "forbidden = {",
            "    'app.providers.livekit_dispatch.livekit',",
            "    'app.providers.summaries.gemini',",
            "    'app.providers.telephony.factory',",
            "}",
            "loaded = forbidden.intersection(sys.modules)",
            "if loaded:",
            "    raise SystemExit(f'Eager topic providers: {sorted(loaded)}')",
        )
    )

    result = subprocess.run(
        [sys.executable, "-E", "-c", script],
        cwd=API_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_registry_exactly_matches_payload_schema_topics() -> None:
    handlers = build_outbox_handlers(
        session_factory=object(),
        telephony_provider=object(),
        subscription_provider=object(),
        livekit_dispatch_provider=object(),
        summary_provider=object(),
        recording_provider=object(),
        storage_provider=object(),
        observability=object(),
        dispatch_token_config=DispatchTokenConfig(secret="captured", ttl_seconds=60),
        livekit_agent_name="captured-agent",
        activation_flow_enabled=True,
        max_call_duration_seconds=321,
        now=lambda: None,
    )
    assert SUPPORTED_OUTBOX_TOPICS == frozenset(REFERENCE_PAYLOAD_FIELDS)
    assert frozenset(handlers) == SUPPORTED_OUTBOX_TOPICS
    for handler in handlers.values():
        required = [
            parameter.name
            for parameter in inspect.signature(handler).parameters.values()
            if parameter.default is inspect.Parameter.empty
        ]
        assert required == ["event"]


def test_worker_settings_imports_with_the_complete_registry() -> None:
    script = """
from app.services.outbox_service import SUPPORTED_OUTBOX_TOPICS
from app.composition import runtime, workers
from app.workers import arq_worker
from app.workers.outbox import delivery
from app.workers.outbox.registry import build_outbox_handlers

if arq_worker.on_background_startup is None:
    raise SystemExit("Background worker startup hook is missing")
if not callable(build_outbox_handlers):
    raise SystemExit("Outbox registry builder is missing")
if not callable(delivery.deliver_outbox_batch):
    raise SystemExit("Outbox delivery engine is missing")
if not callable(workers.build_background_worker_runtime):
    raise SystemExit("Background composition builder is missing")
if runtime.BackgroundWorkerRuntime is None:
    raise SystemExit("Background runtime type is missing")
"""

    completed = subprocess.run(
        [sys.executable, "-E", "-c", script],
        cwd=API_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    if completed.returncode != 0:
        pytest.fail(completed.stderr)


@pytest.mark.anyio
async def test_injected_handlers_bypass_default_registry(
    db_session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del monkeypatch
    operation_id = uuid4()
    outbox_event = await OutboxService(db_session).add(
        topic="recording.reconcile",
        aggregate_type="recording-egress-operation",
        aggregate_id=operation_id,
        idempotency_key=f"recording.reconcile:{operation_id}:architecture",
        payload={"operation_id": str(operation_id)},
    )
    event_id = outbox_event.id
    await db_session.commit()
    session_factory = async_sessionmaker(
        db_session.bind,
        expire_on_commit=False,
    )
    observed_events: list[tuple[str, str]] = []

    async def injected_recording_handler(event: OutboxEvent) -> None:
        observed_events.append((str(event.id), event.topic))

    result = await delivery.deliver_outbox_batch(
        session_factory=session_factory,
        handlers={
            "recording.reconcile": injected_recording_handler,
        },
        observability=object(),
        now=lambda: datetime.now(UTC),
    )

    assert result == {
        "claimed": 1,
        "delivered": 1,
        "retried": 0,
        "failed": 0,
    }
    assert observed_events == [(str(event_id), "recording.reconcile")]
    db_session.expire_all()
    stored_event = await db_session.get(OutboxEvent, event_id)
    assert stored_event is not None
    assert stored_event.status == "delivered"
