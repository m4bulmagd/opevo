from datetime import UTC, datetime, timedelta
from importlib.util import find_spec

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.call import Call
from app.models.outbox_event import OutboxEvent
from app.models.recording_egress_operation import RecordingEgressOperation
from app.models.usage_ledger import UsageLedger
from app.models.user import User
from app.services import call_reconciliation_service as reconciliation_module
from app.services.recording_lifecycle_service import RecordingLifecycleService
from tests.reconciliation_settings import TEST_RECONCILIATION_SETTINGS


HAS_RECONCILIATION = find_spec("app.services.call_reconciliation_service") is not None
if HAS_RECONCILIATION:
    from app.services.call_reconciliation_service import CallReconciliationService
else:
    CallReconciliationService = None


def test_reconciliation_module_exists() -> None:
    assert HAS_RECONCILIATION


async def _user(db_session, suffix: str) -> User:
    user = User(
        external_user_id=f"reconcile_{suffix}",
        email=f"reconcile_{suffix}@example.com",
    )
    db_session.add(user)
    await db_session.flush()
    return user


async def _add_migrated_pending_recording(
    db_session,
    call: Call,
    *,
    now: datetime,
    suffix: str,
) -> RecordingEgressOperation:
    room_name = f"room-migrated-{suffix}"
    provider_egress_id = f"egress-migrated-{suffix}"
    object_key = f"calls/{call.user_id}/{call.id}.ogg"
    call.livekit_room_id = room_name
    call.recording_object_key = object_key
    call.recording_egress_id = provider_egress_id
    call.recording_url = f"https://recordings.example/{suffix}.ogg"
    operation = RecordingEgressOperation(
        id=call.id,
        call_id=call.id,
        room_name=room_name,
        legacy_incomplete=False,
        expected_object_key=object_key,
        provider_egress_id=provider_egress_id,
        start_state="started",
    )
    db_session.add_all(
        [
            operation,
            OutboxEvent(
                idempotency_key=f"recording.reconcile:{operation.id}:start",
                topic="recording.reconcile",
                aggregate_type="recording-egress-operation",
                aggregate_id=operation.id,
                payload={"operation_id": str(operation.id)},
                next_attempt_at=now + timedelta(days=30),
            ),
        ]
    )
    await db_session.flush()
    return operation


@pytest.mark.skipif(not HAS_RECONCILIATION, reason="reconciliation missing")
@pytest.mark.anyio
async def test_reconciliation_recovers_each_stale_nonterminal_state(
    db_session,
) -> None:
    now = datetime.now(UTC)
    pending_user = await _user(db_session, "pending")
    connected_user = await _user(db_session, "connected")
    ending_user = await _user(db_session, "ending")
    finalizing_user = await _user(db_session, "finalizing")
    pending = Call(
        user_id=pending_user.id,
        status="pending",
        state_changed_at=now - timedelta(seconds=121),
    )
    connected = Call(
        user_id=connected_user.id,
        status="connected",
        started_at=now - timedelta(seconds=3721),
        state_changed_at=now - timedelta(seconds=3721),
    )
    ending = Call(
        user_id=ending_user.id,
        status="ending",
        started_at=now - timedelta(seconds=90),
        ended_at=now - timedelta(seconds=61),
        duration_seconds=29,
        state_changed_at=now - timedelta(seconds=61),
    )
    finalizing = Call(
        user_id=finalizing_user.id,
        status="finalizing",
        started_at=now - timedelta(seconds=400),
        ended_at=now - timedelta(seconds=301),
        duration_seconds=99,
        finalization_attempt_count=1,
        state_changed_at=now - timedelta(seconds=301),
    )
    db_session.add_all(
        [
            pending,
            connected,
            ending,
            finalizing,
            UsageLedger(
                user_id=ending_user.id,
                event_type="subscription_activated",
                source_id="in_reconcile_ending",
                minutes_delta=5,
                balance_after=5,
            ),
            UsageLedger(
                user_id=finalizing_user.id,
                event_type="subscription_activated",
                source_id="in_reconcile_finalizing",
                minutes_delta=5,
                balance_after=5,
            ),
        ]
    )
    await db_session.commit()
    ids = [pending.id, connected.id, ending.id, finalizing.id]
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    result = await CallReconciliationService(
        factory, settings=TEST_RECONCILIATION_SETTINGS
    ).reconcile(now, limit=100)

    db_session.expire_all()
    stored = [await db_session.get(Call, call_id) for call_id in ids]
    assert stored[0].status == "failed"
    assert stored[0].failure_code == "dispatch_timeout"
    assert stored[1].status == "ending"
    assert stored[2].status == "completed"
    assert stored[2].finalization_attempt_count == 1
    assert stored[3].status == "completed"
    assert stored[3].finalization_attempt_count == 2
    assert result.failed == 1
    assert result.recovered == 3


@pytest.mark.skipif(not HAS_RECONCILIATION, reason="reconciliation missing")
@pytest.mark.anyio
async def test_stale_pending_failure_requests_stop_for_migrated_recording(
    db_session,
) -> None:
    now = datetime(2026, 7, 19, 12, 0, tzinfo=UTC)
    user = await _user(db_session, "pending_migrated_recording")
    call = Call(
        user_id=user.id,
        status="pending",
        state_changed_at=now - timedelta(seconds=121),
    )
    db_session.add(call)
    await db_session.flush()
    operation = await _add_migrated_pending_recording(
        db_session,
        call,
        now=now,
        suffix="stale-timeout",
    )
    call_id = call.id
    operation_id = operation.id
    await db_session.commit()
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    await CallReconciliationService(
        factory, settings=TEST_RECONCILIATION_SETTINGS
    ).reconcile(now)

    db_session.expire_all()
    stored_call = await db_session.get(Call, call_id)
    stored_operation = await db_session.get(RecordingEgressOperation, operation_id)
    stop_events = (
        await db_session.scalars(
            select(OutboxEvent).where(
                OutboxEvent.idempotency_key
                == f"recording.reconcile:{operation_id}:stop"
            )
        )
    ).all()
    assert stored_call is not None
    assert stored_call.status == "failed"
    assert stored_call.failure_code == "dispatch_timeout"
    assert stored_operation is not None
    assert stored_operation.id == operation_id
    assert stored_operation.provider_egress_id == "egress-migrated-stale-timeout"
    assert stored_operation.stop_requested_at is not None
    assert stored_operation.stop_requested_at.replace(tzinfo=UTC) == now
    assert len(stop_events) == 1
    assert stop_events[0].payload == {"operation_id": str(operation_id)}


@pytest.mark.skipif(not HAS_RECONCILIATION, reason="reconciliation missing")
@pytest.mark.anyio
async def test_stale_pending_failure_rolls_back_when_recording_stop_fails(
    db_session,
    monkeypatch,
) -> None:
    now = datetime(2026, 7, 19, 12, 0, tzinfo=UTC)
    changed_at = now - timedelta(seconds=121)
    user = await _user(db_session, "pending_stop_rollback")
    call = Call(
        user_id=user.id,
        status="pending",
        state_changed_at=changed_at,
    )
    db_session.add(call)
    await db_session.flush()
    operation = await _add_migrated_pending_recording(
        db_session,
        call,
        now=now,
        suffix="stale-rollback",
    )
    call_id = call.id
    operation_id = operation.id
    await db_session.commit()
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    async def fail_request_stop(self, _call):
        raise RuntimeError("forced recording stop failure")

    monkeypatch.setattr(
        reconciliation_module.RecordingLifecycleService,
        "request_stop",
        fail_request_stop,
    )

    with pytest.raises(RuntimeError, match="forced recording stop failure"):
        await CallReconciliationService(
            factory, settings=TEST_RECONCILIATION_SETTINGS
        ).reconcile(now)

    db_session.expire_all()
    stored_call = await db_session.get(Call, call_id)
    stored_operation = await db_session.get(RecordingEgressOperation, operation_id)
    stop_event = await db_session.scalar(
        select(OutboxEvent).where(
            OutboxEvent.idempotency_key == f"recording.reconcile:{operation_id}:stop"
        )
    )
    assert stored_call is not None
    assert stored_call.status == "pending"
    assert stored_call.failure_code is None
    assert stored_call.last_reconciled_at is None
    assert stored_call.state_changed_at.replace(tzinfo=UTC) == changed_at
    assert stored_operation is not None
    assert stored_operation.stop_requested_at is None
    assert stop_event is None


@pytest.mark.skipif(not HAS_RECONCILIATION, reason="reconciliation missing")
@pytest.mark.anyio
async def test_reconciliation_exhausts_uncharged_but_repairs_charged_call(
    db_session,
) -> None:
    now = datetime.now(UTC)
    uncharged_user = await _user(db_session, "uncharged")
    charged_user = await _user(db_session, "charged")
    uncharged = Call(
        user_id=uncharged_user.id,
        status="finalizing",
        duration_seconds=1,
        finalization_attempt_count=5,
        state_changed_at=now - timedelta(seconds=301),
    )
    charged = Call(
        user_id=charged_user.id,
        status="finalizing",
        duration_seconds=1,
        finalization_attempt_count=5,
        state_changed_at=now - timedelta(seconds=301),
    )
    db_session.add_all([uncharged, charged])
    await db_session.flush()
    db_session.add(
        UsageLedger(
            user_id=charged_user.id,
            call_id=charged.id,
            event_type="call_completed",
            minutes_delta=-1,
            balance_after=0,
        )
    )
    await db_session.commit()
    uncharged_id = uncharged.id
    charged_id = charged.id
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    await CallReconciliationService(
        factory, settings=TEST_RECONCILIATION_SETTINGS
    ).reconcile(now, limit=100)

    db_session.expire_all()
    uncharged_stored = await db_session.get(Call, uncharged_id)
    charged_stored = await db_session.get(Call, charged_id)
    assert uncharged_stored.status == "failed"
    assert uncharged_stored.failure_code == "finalization_exhausted"
    assert charged_stored.status == "completed"
    assert charged_stored.finalization_attempt_count == 5


@pytest.mark.skipif(not HAS_RECONCILIATION, reason="reconciliation missing")
@pytest.mark.anyio
async def test_reconciliation_ignores_fresh_and_terminal_calls(
    db_session,
) -> None:
    now = datetime.now(UTC)
    users = [await _user(db_session, suffix) for suffix in ("fresh", "done", "failed")]
    calls = [
        Call(
            user_id=users[0].id,
            status="ending",
            duration_seconds=1,
            state_changed_at=now - timedelta(seconds=59),
        ),
        Call(
            user_id=users[1].id,
            status="completed",
            duration_seconds=1,
            state_changed_at=now - timedelta(days=1),
        ),
        Call(
            user_id=users[2].id,
            status="failed",
            failure_code="dispatch_timeout",
            state_changed_at=now - timedelta(days=1),
        ),
    ]
    db_session.add_all(calls)
    await db_session.commit()
    identities = [(call.id, call.status, call.state_changed_at) for call in calls]
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    result = await CallReconciliationService(
        factory, settings=TEST_RECONCILIATION_SETTINGS
    ).reconcile(now, limit=100)

    assert result.recovered == result.failed == 0
    db_session.expire_all()
    for call_id, status, changed_at in identities:
        stored = await db_session.get(Call, call_id)
        assert stored.status == status
        assert stored.state_changed_at.replace(tzinfo=UTC) == changed_at


@pytest.mark.skipif(not HAS_RECONCILIATION, reason="reconciliation missing")
@pytest.mark.anyio
async def test_reconciliation_honors_limit_and_exact_pending_boundary(
    db_session,
) -> None:
    now = datetime.now(UTC)
    users = [await _user(db_session, suffix) for suffix in ("limit_a", "limit_b")]
    calls = [
        Call(
            user_id=user.id,
            status="pending",
            state_changed_at=now - timedelta(seconds=120),
        )
        for user in users
    ]
    db_session.add_all(calls)
    await db_session.commit()
    call_ids = [call.id for call in calls]
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    result = await CallReconciliationService(
        factory, settings=TEST_RECONCILIATION_SETTINGS
    ).reconcile(now, limit=1)

    assert result.scanned == result.failed == 1
    db_session.expire_all()
    statuses = [(await db_session.get(Call, call_id)).status for call_id in call_ids]
    assert statuses.count("failed") == 1
    assert statuses.count("pending") == 1


@pytest.mark.skipif(not HAS_RECONCILIATION, reason="reconciliation missing")
@pytest.mark.anyio
async def test_connected_recovery_clamps_end_and_duration_to_operational_bound(
    db_session,
) -> None:
    now = datetime.now(UTC)
    user = await _user(db_session, "bounded_connected")
    started_at = now - timedelta(days=2)
    call = Call(
        user_id=user.id,
        status="connected",
        started_at=started_at,
        state_changed_at=started_at,
    )
    db_session.add(call)
    await db_session.commit()
    call_id = call.id
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    await CallReconciliationService(
        factory, settings=TEST_RECONCILIATION_SETTINGS
    ).reconcile(now)

    db_session.expire_all()
    stored = await db_session.get(Call, call_id)
    assert stored.status == "ending"
    assert stored.duration_seconds == 3720
    assert stored.ended_at.replace(tzinfo=UTC) == started_at + timedelta(seconds=3720)


@pytest.mark.skipif(not HAS_RECONCILIATION, reason="reconciliation missing")
@pytest.mark.anyio
async def test_ending_recovery_derives_missing_duration_at_exact_boundary(
    db_session,
) -> None:
    now = datetime.now(UTC)
    user = await _user(db_session, "missing_duration")
    call = Call(
        user_id=user.id,
        status="ending",
        started_at=now - timedelta(seconds=90),
        ended_at=now - timedelta(seconds=60),
        duration_seconds=None,
        state_changed_at=now - timedelta(seconds=60),
    )
    db_session.add_all(
        [
            call,
            UsageLedger(
                user_id=user.id,
                event_type="subscription_activated",
                source_id="in_missing_duration",
                minutes_delta=1,
                balance_after=1,
            ),
        ]
    )
    await db_session.commit()
    call_id = call.id
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    await CallReconciliationService(
        factory, settings=TEST_RECONCILIATION_SETTINGS
    ).reconcile(now)

    db_session.expire_all()
    stored = await db_session.get(Call, call_id)
    assert stored.status == "completed"
    assert stored.duration_seconds == 30


@pytest.mark.anyio
async def test_ending_recovery_persists_recording_stop_before_phase_b(
    db_session,
) -> None:
    now = datetime.now(UTC)
    user = await _user(db_session, "recording_stop")
    call = Call(
        user_id=user.id,
        status="connected",
        livekit_room_id="room-stale-ending",
        started_at=now - timedelta(seconds=90),
    )
    db_session.add(call)
    await db_session.flush()
    operation = await RecordingLifecycleService(db_session).prepare_start(call)
    operation_id = operation.id
    call_id = call.id
    call.status = "ending"
    call.ended_at = now - timedelta(seconds=61)
    call.duration_seconds = 29
    call.state_changed_at = now - timedelta(seconds=61)
    db_session.add(
        UsageLedger(
            user_id=user.id,
            event_type="subscription_activated",
            source_id="in_reconcile_recording_stop",
            minutes_delta=1,
            balance_after=1,
        )
    )
    await db_session.commit()
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    await CallReconciliationService(
        factory, settings=TEST_RECONCILIATION_SETTINGS
    ).reconcile(now)

    db_session.expire_all()
    stored_operation = await db_session.get(RecordingEgressOperation, operation_id)
    event = await db_session.scalar(
        select(OutboxEvent).where(
            OutboxEvent.idempotency_key == f"recording.reconcile:{operation_id}:stop"
        )
    )
    stored_call = await db_session.get(Call, call_id)
    assert stored_call is not None
    assert stored_call.status == "completed"
    assert stored_operation is not None
    assert stored_operation.stop_requested_at is not None
    assert event is not None
    assert event.payload == {"operation_id": str(operation_id)}


@pytest.mark.skipif(not HAS_RECONCILIATION, reason="reconciliation missing")
@pytest.mark.anyio
async def test_phase_b_failure_leaves_committed_new_generation_and_lease(
    db_session,
    monkeypatch,
    caplog,
) -> None:
    now = datetime.now(UTC)
    user = await _user(db_session, "deferred")
    call = Call(
        user_id=user.id,
        status="finalizing",
        duration_seconds=1,
        finalization_attempt_count=1,
        state_changed_at=now - timedelta(seconds=300),
    )
    db_session.add(call)
    await db_session.commit()
    call_id = call.id
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    async def fail_phase_b(self, _call_id, *, generation):
        assert generation == 2
        raise RuntimeError(
            "poisoned call TRANSCRIPT_SENTINEL PHONE_SENTINEL_+33123456789"
        )

    monkeypatch.setattr(
        reconciliation_module.CallLifecycleService,
        "complete_finalization",
        fail_phase_b,
    )

    with caplog.at_level("WARNING"):
        result = await CallReconciliationService(
            factory, settings=TEST_RECONCILIATION_SETTINGS
        ).reconcile(now)

    assert result.recovered == 0
    assert result.deferred == 1
    db_session.expire_all()
    stored = await db_session.get(Call, call_id)
    assert stored.status == "finalizing"
    assert stored.finalization_attempt_count == 2
    assert stored.state_changed_at.replace(tzinfo=UTC) == now
    assert stored.last_reconciled_at.replace(tzinfo=UTC) == now
    assert "poisoned call" not in caplog.text
    assert "TRANSCRIPT_SENTINEL" not in caplog.text
    assert "PHONE_SENTINEL" not in caplog.text
    assert f"call_id={call_id}" in caplog.text
    assert "generation=2" in caplog.text
    assert "error_type=RuntimeError" in caplog.text
    assert all(record.exc_info is None for record in caplog.records)
