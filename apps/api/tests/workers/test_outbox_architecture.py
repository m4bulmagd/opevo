from pathlib import Path
import subprocess
import sys
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
from app.workers.outbox.account_deactivation import deliver_account_deactivation
from app.workers.outbox.customer_dispatch import deliver_livekit_dispatch
from app.workers.outbox.delivery import get_default_outbox_handlers
from app.workers.outbox.phone import deliver_phone_provision, deliver_phone_routing
from app.workers.outbox.post_call import (
    deliver_recording_reconcile,
    deliver_summary_generate,
)
from app.workers.outbox.provider_cleanup import deliver_provider_cleanup
from app.workers.outbox.registry import DEFAULT_OUTBOX_HANDLERS
from app.workers.outbox.verification_dispatch import (
    deliver_livekit_verification_dispatch,
)


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
    expected_handlers = {
        "account.deactivate": deliver_account_deactivation,
        "provider.cleanup": deliver_provider_cleanup,
        "phone.provision": deliver_phone_provision,
        "phone.enable": deliver_phone_routing,
        "phone.disable": deliver_phone_routing,
        "livekit.dispatch": deliver_livekit_dispatch,
        "livekit.verification_dispatch": (
            deliver_livekit_verification_dispatch
        ),
        "summary.generate": deliver_summary_generate,
        "recording.reconcile": deliver_recording_reconcile,
    }

    assert SUPPORTED_OUTBOX_TOPICS == frozenset(REFERENCE_PAYLOAD_FIELDS)
    assert DEFAULT_OUTBOX_HANDLERS == expected_handlers


def test_default_handler_lookup_returns_the_explicit_registry() -> None:
    assert get_default_outbox_handlers() is DEFAULT_OUTBOX_HANDLERS


def test_worker_settings_imports_with_the_complete_registry() -> None:
    script = """
from app.services.outbox_service import SUPPORTED_OUTBOX_TOPICS
from app.workers import arq_worker
from app.workers.outbox.registry import DEFAULT_OUTBOX_HANDLERS

if arq_worker.on_background_startup is None:
    raise SystemExit("Background worker startup hook is missing")
if frozenset(DEFAULT_OUTBOX_HANDLERS) != SUPPORTED_OUTBOX_TOPICS:
    raise SystemExit("Default outbox registry is incomplete")
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
    def fail_default_lookup():
        pytest.fail("default registry must not load when handlers are injected")

    monkeypatch.setattr(
        delivery,
        "get_default_outbox_handlers",
        fail_default_lookup,
    )
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

    async def injected_recording_handler(_ctx, event: OutboxEvent) -> None:
        observed_events.append((str(event.id), event.topic))

    result = await delivery.outbox_delivery_job(
        {
            "session_factory": session_factory,
            "outbox_handlers": {
                "recording.reconcile": injected_recording_handler,
            },
        }
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
