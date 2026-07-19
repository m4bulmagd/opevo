"""Durable post-call orchestration tests.

Provider execution is covered by ``test_post_call_outbox_handlers.py``.  These
tests keep the former post-call suite focused on the provider-free transaction
that creates the durable work.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.models.call import Call
from app.models.notification import Notification
from app.models.outbox_event import OutboxEvent
from app.models.recording_egress_operation import RecordingEgressOperation
from app.models.usage_ledger import UsageLedger
from app.services.call_lifecycle_service import CallLifecycleService
from app.services.recording_lifecycle_service import RecordingLifecycleService


async def _ending_call(db_session, active_user, *, balance: int = 3) -> Call:
    now = datetime.now(UTC)
    call = Call(
        user_id=active_user.id,
        status="ending",
        started_at=now - timedelta(seconds=61),
        ended_at=now,
        duration_seconds=61,
        recording_egress_id="egress-post-call",
    )
    db_session.add_all(
        [
            call,
            UsageLedger(
                user_id=active_user.id,
                event_type="subscription_activated",
                source_id=f"in_post_call_{call.id}",
                minutes_delta=balance,
                balance_after=balance,
            ),
        ]
    )
    await db_session.commit()
    return call


@pytest.mark.anyio
async def test_finalization_persists_non_recording_reference_work_only(
    db_session,
    active_user,
) -> None:
    call = await _ending_call(db_session, active_user)
    lifecycle = CallLifecycleService(db_session)
    recording_operation_count = await db_session.scalar(
        select(func.count()).select_from(RecordingEgressOperation)
    )
    recording_event_count = await db_session.scalar(
        select(func.count())
        .select_from(OutboxEvent)
        .where(OutboxEvent.topic.like("recording.%"))
    )

    claim = await lifecycle.claim_finalization(call.id)
    result = await lifecycle.complete_finalization(
        call.id,
        generation=claim.generation,
    )

    assert result.minutes_charged == 2
    stored = await db_session.get(Call, call.id)
    assert stored.status == "completed"
    notification = await db_session.scalar(
        select(Notification).where(Notification.call_id == call.id)
    )
    assert notification.payload == {
        "event": "call_completed",
        "call_id": str(call.id),
    }
    intents = list(
        (
            await db_session.execute(
                select(OutboxEvent)
                .where(OutboxEvent.aggregate_id == call.id)
                .order_by(OutboxEvent.topic)
            )
        ).scalars()
    )
    assert [intent.topic for intent in intents] == ["summary.generate"]
    assert all(intent.payload == {"call_id": str(call.id)} for intent in intents)
    assert await db_session.scalar(
        select(func.count()).select_from(RecordingEgressOperation)
    ) == recording_operation_count
    assert await db_session.scalar(
        select(func.count())
        .select_from(OutboxEvent)
        .where(OutboxEvent.topic.like("recording.%"))
    ) == recording_event_count


@pytest.mark.anyio
async def test_post_call_retry_is_idempotent(db_session, active_user) -> None:
    call = await _ending_call(db_session, active_user)
    lifecycle = CallLifecycleService(db_session)
    claim = await lifecycle.claim_finalization(call.id)

    first = await lifecycle.complete_finalization(
        call.id,
        generation=claim.generation,
    )
    retry = await lifecycle.complete_finalization(
        call.id,
        generation=claim.generation,
    )

    assert first.already_completed is False
    assert retry.already_completed is True
    assert await db_session.scalar(
        select(func.count())
        .select_from(UsageLedger)
        .where(UsageLedger.call_id == call.id)
    ) == 1
    assert await db_session.scalar(
        select(func.count())
        .select_from(Notification)
        .where(Notification.call_id == call.id)
    ) == 1


@pytest.mark.anyio
async def test_new_recording_lifecycle_never_produces_legacy_stop_topic(
    db_session,
    active_user,
) -> None:
    now = datetime.now(UTC)
    call = Call(
        user_id=active_user.id,
        status="connected",
        livekit_room_id="room-recording-lifecycle-only",
        started_at=now,
    )
    db_session.add(call)
    await db_session.flush()
    lifecycle = RecordingLifecycleService(
        db_session,
        now_provider=lambda: now,
    )
    operation = await lifecycle.prepare_start(call)
    call.status = "completed"
    call.duration_seconds = 1
    await lifecycle.request_stop(call)
    await db_session.commit()

    events = list(
        (
            await db_session.execute(
                select(OutboxEvent).where(
                    OutboxEvent.aggregate_id == operation.id
                )
            )
        ).scalars()
    )
    assert events
    assert all(event.topic == "recording.reconcile" for event in events)
    assert all(event.payload == {"operation_id": str(operation.id)} for event in events)


@pytest.mark.anyio
async def test_minute_exhaustion_creates_phone_disable_intent_without_provider_io(
    db_session,
    active_user,
) -> None:
    call = await _ending_call(db_session, active_user, balance=2)
    lifecycle = CallLifecycleService(db_session)
    claim = await lifecycle.claim_finalization(call.id)

    await lifecycle.complete_finalization(call.id, generation=claim.generation)

    intent = await db_session.scalar(
        select(OutboxEvent).where(OutboxEvent.topic == "phone.disable")
    )
    assert intent is not None
    assert intent.aggregate_type == "user"
    assert intent.aggregate_id == active_user.id
    assert intent.payload == {"user_id": str(active_user.id)}
