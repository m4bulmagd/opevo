from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models.call import Call
from app.models.notification import Notification
from app.models.outbox_event import OutboxEvent
from app.models.recording_egress_operation import RecordingEgressOperation
from app.models.usage_ledger import UsageLedger
from app.services import call_lifecycle_service as lifecycle_module
from app.services.call_lifecycle_service import CallLifecycleService
from app.services.recording_lifecycle_service import RecordingLifecycleService


HAS_TWO_PHASE_LIFECYCLE = all(
    hasattr(CallLifecycleService, name)
    for name in (
        "end_from_agent",
        "claim_finalization",
        "complete_finalization",
    )
)


def test_lifecycle_exposes_two_phase_interface() -> None:
    assert HAS_TWO_PHASE_LIFECYCLE


@pytest.mark.skipif(not HAS_TWO_PHASE_LIFECYCLE, reason="two-phase lifecycle missing")
@pytest.mark.anyio
async def test_agent_end_freezes_first_facts_and_repairs_missing_join(
    db_session,
    active_user,
) -> None:
    created_at = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    call = Call(
        user_id=active_user.id,
        status="pending",
        created_at=created_at,
    )
    db_session.add(call)
    await db_session.commit()
    ended_at = created_at + timedelta(seconds=90)
    service = CallLifecycleService(db_session)

    first = await service.end_from_agent(
        call_id=call.id,
        duration_seconds=90,
        ended_at=ended_at,
    )
    await db_session.commit()
    duplicate = await service.end_from_agent(
        call_id=call.id,
        duration_seconds=999,
        ended_at=ended_at + timedelta(hours=1),
    )
    await db_session.commit()

    assert first.status == duplicate.status == "ending"
    assert duplicate.ended_at.replace(tzinfo=UTC) == ended_at
    assert duplicate.duration_seconds == 90
    assert duplicate.started_at.replace(tzinfo=UTC) == created_at


@pytest.mark.skipif(not HAS_TWO_PHASE_LIFECYCLE, reason="two-phase lifecycle missing")
@pytest.mark.anyio
async def test_sip_leave_ends_connected_call_without_recording_metadata(
    db_session,
    active_user,
) -> None:
    started_at = datetime.now(UTC) - timedelta(seconds=12)
    call = Call(
        user_id=active_user.id,
        status="connected",
        started_at=started_at,
        recording_egress_id=None,
    )
    db_session.add(call)
    await db_session.commit()
    ended_at = datetime.now(UTC)

    result = await CallLifecycleService(db_session).end_from_sip(
        call_id=call.id,
        ended_at=ended_at,
    )
    await db_session.commit()

    assert result.status == "ending"
    assert result.ended_at.replace(tzinfo=UTC) == ended_at
    assert result.duration_seconds >= 12


@pytest.mark.skipif(not HAS_TWO_PHASE_LIFECYCLE, reason="two-phase lifecycle missing")
@pytest.mark.anyio
async def test_sip_leave_before_connection_fails_safely(
    db_session,
    active_user,
) -> None:
    call = Call(user_id=active_user.id, status="pending")
    db_session.add(call)
    await db_session.commit()

    result = await CallLifecycleService(db_session).end_from_sip(call_id=call.id)
    await db_session.commit()

    assert result.status == "failed"
    assert result.failure_code == "caller_left_before_connect"
    assert result.duration_seconds == 0


@pytest.mark.anyio
async def test_agent_end_requests_operation_scoped_stop_in_same_transaction(
    db_session,
    active_user,
) -> None:
    call = Call(
        user_id=active_user.id,
        status="connected",
        livekit_room_id="room-durable-end",
        started_at=datetime.now(UTC) - timedelta(seconds=7),
    )
    db_session.add(call)
    await db_session.flush()
    operation = await RecordingLifecycleService(db_session).prepare_start(call)
    await db_session.commit()

    ended = await CallLifecycleService(db_session).end_from_agent(
        call_id=call.id,
        duration_seconds=7,
    )
    await db_session.commit()

    stored_operation = await db_session.get(RecordingEgressOperation, operation.id)
    stop_event = await db_session.scalar(
        select(OutboxEvent).where(
            OutboxEvent.idempotency_key
            == f"recording.reconcile:{operation.id}:stop"
        )
    )
    assert ended.status == "ending"
    assert stored_operation is not None
    assert stored_operation.stop_requested_at is not None
    assert stop_event is not None
    assert stop_event.aggregate_id == operation.id
    assert stop_event.payload == {"operation_id": str(operation.id)}


@pytest.mark.anyio
async def test_repeated_terminal_completion_repairs_missing_stop_without_rewriting_facts(
    db_session,
    active_user,
) -> None:
    frozen_end = datetime(2026, 7, 19, 10, 0, tzinfo=UTC)
    call = Call(
        user_id=active_user.id,
        status="connected",
        livekit_room_id="room-terminal-stop-repair",
        started_at=frozen_end - timedelta(seconds=11),
    )
    db_session.add(call)
    await db_session.flush()
    operation = await RecordingLifecycleService(db_session).prepare_start(call)
    service = CallLifecycleService(db_session)
    await service.end_from_agent(
        call_id=call.id,
        duration_seconds=11,
        ended_at=frozen_end,
    )
    await db_session.commit()
    stop_event = await db_session.scalar(
        select(OutboxEvent).where(
            OutboxEvent.idempotency_key
            == f"recording.reconcile:{operation.id}:stop"
        )
    )
    assert stop_event is not None
    operation.stop_requested_at = None
    await db_session.delete(stop_event)
    await db_session.commit()

    repeated = await service.end_from_agent(
        call_id=call.id,
        duration_seconds=999,
        ended_at=frozen_end + timedelta(hours=1),
    )
    await db_session.commit()

    await db_session.refresh(operation)
    assert repeated.status == "ending"
    assert repeated.ended_at.replace(tzinfo=UTC) == frozen_end
    assert repeated.duration_seconds == 11
    assert operation.stop_requested_at is not None
    assert await db_session.scalar(
        select(OutboxEvent).where(
            OutboxEvent.idempotency_key
            == f"recording.reconcile:{operation.id}:stop"
        )
    ) is not None


@pytest.mark.skipif(not HAS_TWO_PHASE_LIFECYCLE, reason="two-phase lifecycle missing")
@pytest.mark.anyio
async def test_two_phase_finalization_commits_only_reference_intents(
    db_session,
    active_user,
) -> None:
    call = Call(
        user_id=active_user.id,
        status="ending",
        started_at=datetime.now(UTC) - timedelta(seconds=61),
        ended_at=datetime.now(UTC),
        duration_seconds=61,
        recording_egress_id="egress-opaque",
    )
    db_session.add_all(
        [
            call,
            UsageLedger(
                user_id=active_user.id,
                event_type="subscription_activated",
                source_id="in_two_phase",
                minutes_delta=2,
                balance_after=2,
            ),
        ]
    )
    await db_session.commit()
    service = CallLifecycleService(db_session)

    claim = await service.claim_finalization(call.id)

    assert claim.generation == 1
    assert db_session.in_transaction() is False
    phase_a = await db_session.get(Call, call.id)
    assert phase_a.status == "finalizing"
    assert phase_a.finalization_attempt_count == 1

    result = await service.complete_finalization(
        call.id,
        generation=claim.generation,
    )

    assert result.already_completed is False
    assert db_session.in_transaction() is False
    stored = await db_session.get(Call, call.id)
    assert stored.status == "completed"
    assert stored.minutes_charged == 2
    notifications = list(
        (
            await db_session.execute(
                select(Notification).where(Notification.call_id == call.id)
            )
        ).scalars()
    )
    assert len(notifications) == 1
    assert notifications[0].payload == {
        "event": "call_completed",
        "call_id": str(call.id),
    }
    intents = list(
        (
            await db_session.execute(
                select(OutboxEvent)
                .where(OutboxEvent.payload == {"call_id": str(call.id)})
                .order_by(OutboxEvent.topic)
            )
        ).scalars()
    )
    assert [(intent.topic, intent.aggregate_type) for intent in intents] == [
        ("summary.generate", "call-summary"),
    ]
    phone_disable = await db_session.scalar(
        select(OutboxEvent).where(OutboxEvent.topic == "phone.disable")
    )
    assert phone_disable is not None
    assert phone_disable.aggregate_type == "user"
    assert phone_disable.aggregate_id == active_user.id
    assert phone_disable.payload == {"user_id": str(active_user.id)}


@pytest.mark.skipif(not HAS_TWO_PHASE_LIFECYCLE, reason="two-phase lifecycle missing")
@pytest.mark.anyio
async def test_stale_generation_cannot_complete_newer_attempt(
    db_session,
    active_user,
) -> None:
    call = Call(
        user_id=active_user.id,
        status="finalizing",
        duration_seconds=1,
        finalization_attempt_count=2,
    )
    db_session.add(call)
    await db_session.commit()
    call_id = call.id

    result = await CallLifecycleService(db_session).complete_finalization(
        call_id,
        generation=1,
    )

    assert result.stale_generation is True
    assert (await db_session.get(Call, call_id)).status == "finalizing"
    assert await db_session.scalar(
        select(UsageLedger).where(UsageLedger.call_id == call_id)
    ) is None


@pytest.mark.skipif(not HAS_TWO_PHASE_LIFECYCLE, reason="two-phase lifecycle missing")
@pytest.mark.anyio
async def test_forced_phase_b_rollback_leaves_no_partial_rows(
    db_session,
    active_user,
    monkeypatch,
) -> None:
    call = Call(
        user_id=active_user.id,
        status="finalizing",
        duration_seconds=1,
        finalization_attempt_count=1,
    )
    db_session.add(call)
    await db_session.commit()
    call_id = call.id

    original_add = lifecycle_module.OutboxService.add

    async def fail_after_first_intent(self, **kwargs):
        await original_add(self, **kwargs)
        raise RuntimeError("forced rollback")

    monkeypatch.setattr(lifecycle_module.OutboxService, "add", fail_after_first_intent)
    with pytest.raises(RuntimeError, match="forced rollback"):
        await CallLifecycleService(db_session).complete_finalization(
            call_id,
            generation=1,
        )

    assert db_session.in_transaction() is False
    assert (await db_session.get(Call, call_id)).status == "finalizing"
    assert await db_session.scalar(
        select(UsageLedger).where(UsageLedger.call_id == call_id)
    ) is None
    assert await db_session.scalar(
        select(Notification).where(Notification.call_id == call_id)
    ) is None
    assert await db_session.scalar(
        select(OutboxEvent).where(OutboxEvent.aggregate_id == call_id)
    ) is None


@pytest.mark.skipif(not HAS_TWO_PHASE_LIFECYCLE, reason="two-phase lifecycle missing")
@pytest.mark.anyio
@pytest.mark.parametrize("missing", ["duration", "started", "ended"])
async def test_direct_finalization_repairs_incomplete_end_facts_from_database(
    db_session,
    active_user,
    missing: str,
) -> None:
    created_at = datetime.now(UTC) - timedelta(seconds=60)
    started_at = created_at + timedelta(seconds=15)
    ended_at = started_at + timedelta(seconds=45)
    call = Call(
        user_id=active_user.id,
        status="ending",
        created_at=created_at,
        state_changed_at=ended_at,
        started_at=None if missing == "started" else started_at,
        ended_at=None if missing == "ended" else ended_at,
        duration_seconds=None if missing == "duration" else 45,
    )
    db_session.add_all(
        [
            call,
            UsageLedger(
                user_id=active_user.id,
                event_type="subscription_activated",
                source_id=f"in_incomplete_end_facts_{missing}",
                minutes_delta=1,
                balance_after=1,
            ),
        ]
    )
    await db_session.commit()
    lifecycle = CallLifecycleService(db_session)

    claim = await lifecycle.claim_finalization(call.id)
    result = await lifecycle.complete_finalization(
        call.id,
        generation=claim.generation,
    )

    assert result.minutes_charged == 1
    stored = await db_session.get(Call, call.id)
    assert stored.status == "completed"
    assert stored.started_at.replace(tzinfo=UTC) == started_at
    assert stored.ended_at.replace(tzinfo=UTC) == ended_at
    assert stored.duration_seconds == 45
