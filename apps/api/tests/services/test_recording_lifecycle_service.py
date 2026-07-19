from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from uuid import UUID

import pytest
from sqlalchemy import event as sqlalchemy_event
from sqlalchemy import func, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.call import Call
from app.models.outbox_event import OutboxEvent
from app.models.recording_egress_operation import RecordingEgressOperation
from app.providers.livekit_recording.base import (
    RecordingEgressResult,
    build_recording_object_key,
)
from app.repositories.call_repository import CallRepository
from app.services.recording_lifecycle_service import (
    RECORDING_AGGREGATE_TYPE,
    RECORDING_START_ERROR_CODES,
    START_RESULT_LEASE,
    RecordingEgressEventFact,
    RecordingLifecycleService,
    RecordingStartClaim,
)


FIXED_NOW = datetime(2026, 7, 19, 12, 0, tzinfo=UTC)


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


async def _call(
    session: AsyncSession,
    *,
    user_id: UUID,
    status: str = "connected",
    room_name: str | None = "room-owned",
    deleted_at: datetime | None = None,
) -> Call:
    call = Call(
        user_id=user_id,
        status=status,
        failure_code="legacy_failure" if status == "failed" else None,
        livekit_room_id=room_name,
        started_at=FIXED_NOW,
        deleted_at=deleted_at,
    )
    session.add(call)
    await session.flush()
    return call


def test_recording_egress_event_fact_is_an_immutable_sanitized_value() -> None:
    fact = RecordingEgressEventFact(
        external_event_id="EV_exact",
        event_type="egress_started",
        egress_id="EG_exact",
        room_name="room-owned",
        status=1,
        object_key="calls/user-id/call-id.ogg",
    )

    assert fact.external_event_id == "EV_exact"
    assert fact.object_key == "calls/user-id/call-id.ogg"
    with pytest.raises(FrozenInstanceError):
        fact.status = 2  # type: ignore[misc]


@pytest.mark.anyio
async def test_prepare_start_persists_prepared_operation_and_delayed_intent(
    db_session: AsyncSession,
    active_user,
) -> None:
    call = await _call(db_session, user_id=active_user.id)

    operation = await RecordingLifecycleService(
        db_session,
        now_provider=lambda: FIXED_NOW,
    ).prepare_start(call)

    assert operation.call_id == call.id
    assert operation.room_name == "room-owned"
    assert operation.legacy_incomplete is False
    assert operation.expected_object_key == build_recording_object_key(
        user_id=call.user_id,
        call_id=call.id,
    )
    assert operation.start_state == "prepared"
    assert operation.start_attempted_at is None
    event = await db_session.scalar(
        select(OutboxEvent).where(
            OutboxEvent.aggregate_type == RECORDING_AGGREGATE_TYPE,
            OutboxEvent.aggregate_id == operation.id,
        )
    )
    assert event is not None
    assert event.idempotency_key == f"recording.reconcile:{operation.id}:start"
    assert event.payload == {"operation_id": str(operation.id)}
    assert _as_utc(event.next_attempt_at) == FIXED_NOW + START_RESULT_LEASE


@pytest.mark.anyio
async def test_prepare_start_relocks_call_then_operation_for_postgresql(
    db_session: AsyncSession,
    active_user,
) -> None:
    call = await _call(db_session, user_id=active_user.id)
    statements = []

    def capture_statement(execute_state) -> None:
        if execute_state.is_select:
            statements.append(execute_state.statement)

    sqlalchemy_event.listen(
        db_session.sync_session, "do_orm_execute", capture_statement
    )
    try:
        await RecordingLifecycleService(
            db_session,
            now_provider=lambda: FIXED_NOW,
        ).prepare_start(call)
    finally:
        sqlalchemy_event.remove(
            db_session.sync_session,
            "do_orm_execute",
            capture_statement,
        )

    compiled = [str(item.compile(dialect=postgresql.dialect())) for item in statements]
    call_lock_index = next(
        index
        for index, sql in enumerate(compiled)
        if "FROM calls" in sql and "FOR UPDATE" in sql
    )
    operation_lock_index = next(
        index
        for index, sql in enumerate(compiled)
        if "FROM recording_egress_operations" in sql and "FOR UPDATE" in sql
    )
    assert call_lock_index < operation_lock_index


@pytest.mark.anyio
async def test_repeated_prepare_preserves_operation_event_and_first_due_time(
    db_session: AsyncSession,
    active_user,
) -> None:
    call = await _call(db_session, user_id=active_user.id)
    current_time = [FIXED_NOW]
    service = RecordingLifecycleService(
        db_session,
        now_provider=lambda: current_time[0],
    )

    first = await service.prepare_start(call)
    current_time[0] = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)
    second = await service.prepare_start(call)

    assert second.id == first.id
    assert (
        await db_session.scalar(
            select(func.count()).select_from(RecordingEgressOperation)
        )
        == 1
    )
    events = list(
        (
            await db_session.scalars(
                select(OutboxEvent).where(
                    OutboxEvent.aggregate_type == RECORDING_AGGREGATE_TYPE,
                    OutboxEvent.aggregate_id == first.id,
                )
            )
        ).all()
    )
    assert len(events) == 1
    assert _as_utc(events[0].next_attempt_at) == FIXED_NOW + START_RESULT_LEASE


@pytest.mark.anyio
async def test_prepare_start_rolls_back_operation_and_event_together(
    db_session: AsyncSession,
    active_user,
) -> None:
    call = await _call(db_session, user_id=active_user.id)
    await db_session.commit()

    await RecordingLifecycleService(
        db_session,
        now_provider=lambda: FIXED_NOW,
    ).prepare_start(call)
    await db_session.rollback()

    assert (
        await db_session.scalar(
            select(func.count()).select_from(RecordingEgressOperation)
        )
        == 0
    )
    assert await db_session.scalar(select(func.count()).select_from(OutboxEvent)) == 0


@pytest.mark.anyio
async def test_prepare_start_rejects_missing_room_without_legacy_marker(
    db_session: AsyncSession,
    active_user,
) -> None:
    call = await _call(db_session, user_id=active_user.id, room_name=None)

    with pytest.raises(ValueError, match="room"):
        await RecordingLifecycleService(
            db_session,
            now_provider=lambda: FIXED_NOW,
        ).prepare_start(call)

    assert (
        await db_session.scalar(
            select(func.count()).select_from(RecordingEgressOperation)
        )
        == 0
    )


@pytest.mark.anyio
async def test_including_deleted_call_locks_are_owner_scoped_and_postgresql_safe(
    db_session: AsyncSession,
    active_user,
) -> None:
    call = await _call(
        db_session,
        user_id=active_user.id,
        status="completed",
        deleted_at=FIXED_NOW,
    )
    statements = []

    def capture_statement(execute_state) -> None:
        if execute_state.is_select:
            statements.append(execute_state.statement)

    sqlalchemy_event.listen(
        db_session.sync_session, "do_orm_execute", capture_statement
    )
    try:
        repository = CallRepository(db_session)
        by_id = await repository.get_by_id_including_deleted_for_update(call.id)
        owned = await repository.get_by_id_for_user_including_deleted_for_update(
            call.id,
            user_id=active_user.id,
        )
    finally:
        sqlalchemy_event.remove(
            db_session.sync_session,
            "do_orm_execute",
            capture_statement,
        )

    assert by_id is call
    assert owned is call
    compiled = [str(item.compile(dialect=postgresql.dialect())) for item in statements]
    assert len(compiled) == 2
    assert all("FROM calls" in sql and "FOR UPDATE" in sql for sql in compiled)
    assert "calls.user_id" in compiled[1]


@pytest.mark.anyio
async def test_begin_start_claims_prepared_operation_once(
    db_session: AsyncSession,
    active_user,
) -> None:
    call = await _call(db_session, user_id=active_user.id)
    service = RecordingLifecycleService(
        db_session,
        now_provider=lambda: FIXED_NOW,
    )
    operation = await service.prepare_start(call)

    claim = await service.begin_start(operation.id)

    assert claim == RecordingStartClaim(
        operation_id=operation.id,
        call_id=call.id,
        room_name="room-owned",
        expected_object_key=operation.expected_object_key,
    )
    assert operation.start_state == "starting"
    assert _as_utc(operation.start_attempted_at) == FIXED_NOW

    repeated = await service.begin_start(operation.id)

    assert repeated is None
    assert operation.start_state == "starting"
    assert _as_utc(operation.start_attempted_at) == FIXED_NOW


@pytest.mark.anyio
async def test_begin_start_discovers_then_locks_call_before_operation(
    db_session: AsyncSession,
    active_user,
) -> None:
    call = await _call(db_session, user_id=active_user.id)
    operation = await RecordingLifecycleService(
        db_session,
        now_provider=lambda: FIXED_NOW,
    ).prepare_start(call)
    operation_id = operation.id
    await db_session.commit()
    db_session.expire_all()
    statements = []

    def capture_statement(execute_state) -> None:
        if execute_state.is_select:
            statements.append(execute_state.statement)

    sqlalchemy_event.listen(
        db_session.sync_session, "do_orm_execute", capture_statement
    )
    try:
        claim = await RecordingLifecycleService(
            db_session,
            now_provider=lambda: FIXED_NOW,
        ).begin_start(operation_id)
    finally:
        sqlalchemy_event.remove(
            db_session.sync_session,
            "do_orm_execute",
            capture_statement,
        )

    assert claim is not None
    compiled = [str(item.compile(dialect=postgresql.dialect())) for item in statements]
    discovery_index = next(
        index
        for index, sql in enumerate(compiled)
        if "FROM recording_egress_operations" in sql and "FOR UPDATE" not in sql
    )
    call_lock_index = next(
        index
        for index, sql in enumerate(compiled)
        if "FROM calls" in sql and "FOR UPDATE" in sql
    )
    operation_lock_index = next(
        index
        for index, sql in enumerate(compiled)
        if "FROM recording_egress_operations" in sql and "FOR UPDATE" in sql
    )
    assert discovery_index < call_lock_index < operation_lock_index


@pytest.mark.anyio
@pytest.mark.parametrize(
    "guard",
    [
        "stopped",
        "delete_requested",
        "non_connected",
        "tombstoned",
        "already_starting",
        "uncertain",
    ],
)
async def test_begin_start_refuses_ineligible_call_or_operation(
    db_session: AsyncSession,
    active_user,
    guard: str,
) -> None:
    call = await _call(db_session, user_id=active_user.id)
    service = RecordingLifecycleService(
        db_session,
        now_provider=lambda: FIXED_NOW,
    )
    operation = await service.prepare_start(call)
    if guard == "stopped":
        operation.stop_requested_at = FIXED_NOW
    elif guard == "delete_requested":
        operation.stop_requested_at = FIXED_NOW
        operation.delete_requested_at = FIXED_NOW
    elif guard == "non_connected":
        call.status = "completed"
    elif guard == "tombstoned":
        call.status = "completed"
        call.deleted_at = FIXED_NOW
    elif guard == "already_starting":
        operation.start_state = "starting"
        operation.start_attempted_at = FIXED_NOW
    elif guard == "uncertain":
        operation.start_state = "uncertain"
        operation.start_attempted_at = FIXED_NOW
    await db_session.flush()

    claim = await service.begin_start(operation.id)

    assert claim is None
    assert operation.start_state != "starting" or guard == "already_starting"
    if guard not in {"already_starting", "uncertain"}:
        assert operation.start_state == "prepared"
        assert operation.start_attempted_at is None


@pytest.mark.anyio
async def test_start_success_persists_identity_projects_and_accelerates(
    db_session: AsyncSession,
    active_user,
) -> None:
    call = await _call(db_session, user_id=active_user.id)
    service = RecordingLifecycleService(
        db_session,
        now_provider=lambda: FIXED_NOW,
    )
    operation = await service.prepare_start(call)
    assert await service.begin_start(operation.id) is not None
    result = RecordingEgressResult(
        egress_id="EG_exact",
        object_key=operation.expected_object_key,
        url="s3://private/recording.ogg",
    )

    recorded = await service.record_start_success(operation.id, result)

    assert recorded is operation
    assert operation.start_state == "started"
    assert operation.provider_egress_id == "EG_exact"
    assert operation.last_error_code is None
    assert call.recording_object_key == operation.expected_object_key
    assert call.recording_egress_id == "EG_exact"
    assert call.recording_url == "s3://private/recording.ogg"
    event = await db_session.scalar(
        select(OutboxEvent).where(
            OutboxEvent.idempotency_key == f"recording.reconcile:{operation.id}:start"
        )
    )
    assert event is not None
    assert _as_utc(event.next_attempt_at) == FIXED_NOW

    repeated = await service.record_start_success(operation.id, result)

    assert repeated is operation
    assert operation.provider_egress_id == "EG_exact"


@pytest.mark.anyio
async def test_late_start_success_projects_to_visible_terminal_call(
    db_session: AsyncSession,
    active_user,
) -> None:
    call = await _call(db_session, user_id=active_user.id)
    service = RecordingLifecycleService(
        db_session,
        now_provider=lambda: FIXED_NOW,
    )
    operation = await service.prepare_start(call)
    assert await service.begin_start(operation.id) is not None
    ended_at = datetime(2026, 7, 19, 12, 1, tzinfo=UTC)
    call.status = "completed"
    call.ended_at = ended_at
    await db_session.flush()

    await service.record_start_success(
        operation.id,
        RecordingEgressResult(
            egress_id="EG_late",
            object_key=operation.expected_object_key,
            url=None,
        ),
    )

    assert call.status == "completed"
    assert _as_utc(call.ended_at) == ended_at
    assert call.recording_egress_id == "EG_late"
    assert call.recording_object_key == operation.expected_object_key


@pytest.mark.anyio
async def test_late_start_success_never_projects_to_tombstone(
    db_session: AsyncSession,
    active_user,
) -> None:
    call = await _call(db_session, user_id=active_user.id)
    service = RecordingLifecycleService(
        db_session,
        now_provider=lambda: FIXED_NOW,
    )
    operation = await service.prepare_start(call)
    assert await service.begin_start(operation.id) is not None
    call.status = "completed"
    call.deleted_at = FIXED_NOW
    await db_session.flush()

    await service.record_start_success(
        operation.id,
        RecordingEgressResult(
            egress_id="EG_private_only",
            object_key=operation.expected_object_key,
            url="s3://must-not-return",
        ),
    )

    assert operation.start_state == "started"
    assert operation.provider_egress_id == "EG_private_only"
    assert call.recording_object_key is None
    assert call.recording_egress_id is None
    assert call.recording_url is None


@pytest.mark.anyio
async def test_late_start_success_preserves_durable_identity_conflict(
    db_session: AsyncSession,
    active_user,
) -> None:
    call = await _call(db_session, user_id=active_user.id)
    service = RecordingLifecycleService(
        db_session,
        now_provider=lambda: FIXED_NOW,
    )
    operation = await service.prepare_start(call)
    assert await service.begin_start(operation.id) is not None
    operation.start_state = "uncertain"
    operation.last_error_code = "recording_identity_conflict"
    call.recording_object_key = None
    call.recording_egress_id = None
    call.recording_url = None
    await db_session.commit()

    recorded = await RecordingLifecycleService(
        db_session,
        now_provider=lambda: FIXED_NOW,
    ).record_start_success(
        operation.id,
        RecordingEgressResult(
            egress_id="EG_trusted_late",
            object_key=operation.expected_object_key,
            url="s3://must-remain-hidden",
        ),
    )

    assert recorded is not None
    assert recorded.start_state == "started"
    assert recorded.provider_egress_id == "EG_trusted_late"
    assert recorded.last_error_code == "recording_identity_conflict"
    assert call.recording_object_key is None
    assert call.recording_egress_id is None
    assert call.recording_url is None


@pytest.mark.anyio
async def test_start_success_rejects_object_key_conflict_without_mutation(
    db_session: AsyncSession,
    active_user,
) -> None:
    call = await _call(db_session, user_id=active_user.id)
    service = RecordingLifecycleService(
        db_session,
        now_provider=lambda: FIXED_NOW,
    )
    operation = await service.prepare_start(call)
    assert await service.begin_start(operation.id) is not None

    with pytest.raises(ValueError, match="object key"):
        await service.record_start_success(
            operation.id,
            RecordingEgressResult(
                egress_id="EG_wrong_path",
                object_key="calls/attacker/object.ogg",
                url=None,
            ),
        )

    assert operation.start_state == "starting"
    assert operation.provider_egress_id is None
    assert call.recording_egress_id is None


@pytest.mark.anyio
async def test_repeated_start_success_rejects_provider_identity_conflict(
    db_session: AsyncSession,
    active_user,
) -> None:
    call = await _call(db_session, user_id=active_user.id)
    service = RecordingLifecycleService(
        db_session,
        now_provider=lambda: FIXED_NOW,
    )
    operation = await service.prepare_start(call)
    assert await service.begin_start(operation.id) is not None
    await service.record_start_success(
        operation.id,
        RecordingEgressResult(
            egress_id="EG_first",
            object_key=operation.expected_object_key,
            url=None,
        ),
    )

    with pytest.raises(ValueError, match="provider identity"):
        await service.record_start_success(
            operation.id,
            RecordingEgressResult(
                egress_id="EG_conflict",
                object_key=operation.expected_object_key,
                url=None,
            ),
        )

    assert operation.start_state == "started"
    assert operation.provider_egress_id == "EG_first"
    assert call.recording_egress_id == "EG_first"


def test_recording_start_error_codes_are_the_exact_bounded_provider_vocabulary() -> (
    None
):
    assert RECORDING_START_ERROR_CODES == frozenset(
        {
            "timeout",
            "rate_limited",
            "unavailable",
            "authentication",
            "validation",
            "conflict",
            "unknown",
        }
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("outcome", "error_code", "expected_state"),
    [
        ("not_started", "validation", "not_started"),
        ("unknown", "timeout", "uncertain"),
    ],
)
async def test_start_error_records_classified_outcome_and_accelerates(
    db_session: AsyncSession,
    active_user,
    outcome: str,
    error_code: str,
    expected_state: str,
) -> None:
    call = await _call(db_session, user_id=active_user.id)
    service = RecordingLifecycleService(
        db_session,
        now_provider=lambda: FIXED_NOW,
    )
    operation = await service.prepare_start(call)
    assert await service.begin_start(operation.id) is not None

    recorded = await service.record_start_error(
        operation.id,
        outcome=outcome,  # type: ignore[arg-type]
        error_code=error_code,
    )

    assert recorded is operation
    assert operation.start_state == expected_state
    assert operation.provider_egress_id is None
    assert operation.last_error_code == error_code
    assert _as_utc(operation.start_attempted_at) == FIXED_NOW
    event = await db_session.scalar(
        select(OutboxEvent).where(
            OutboxEvent.idempotency_key == f"recording.reconcile:{operation.id}:start"
        )
    )
    assert event is not None
    assert _as_utc(event.next_attempt_at) == FIXED_NOW


@pytest.mark.anyio
async def test_uncertain_start_never_regresses_to_not_started_or_starting(
    db_session: AsyncSession,
    active_user,
) -> None:
    call = await _call(db_session, user_id=active_user.id)
    service = RecordingLifecycleService(
        db_session,
        now_provider=lambda: FIXED_NOW,
    )
    operation = await service.prepare_start(call)
    assert await service.begin_start(operation.id) is not None
    await service.record_start_error(
        operation.id,
        outcome="unknown",
        error_code="timeout",
    )

    await service.record_start_error(
        operation.id,
        outcome="not_started",
        error_code="validation",
    )
    claim = await service.begin_start(operation.id)

    assert operation.start_state == "uncertain"
    assert operation.last_error_code == "validation"
    assert claim is None


@pytest.mark.anyio
async def test_late_start_error_preserves_durable_identity_conflict(
    db_session: AsyncSession,
    active_user,
) -> None:
    call = await _call(db_session, user_id=active_user.id)
    service = RecordingLifecycleService(
        db_session,
        now_provider=lambda: FIXED_NOW,
    )
    operation = await service.prepare_start(call)
    assert await service.begin_start(operation.id) is not None
    operation.start_state = "uncertain"
    operation.last_error_code = "recording_identity_conflict"
    await db_session.commit()

    recorded = await RecordingLifecycleService(
        db_session,
        now_provider=lambda: FIXED_NOW,
    ).record_start_error(
        operation.id,
        outcome="unknown",
        error_code="timeout",
    )

    assert recorded is not None
    assert recorded.start_state == "uncertain"
    assert recorded.provider_egress_id is None
    assert recorded.last_error_code == "recording_identity_conflict"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "error_code",
    [
        "timeout",
        "rate_limited",
        "unavailable",
        "authentication",
        "validation",
        "conflict",
        "unknown",
    ],
)
async def test_start_error_accepts_each_bounded_provider_class(
    db_session: AsyncSession,
    active_user,
    error_code: str,
) -> None:
    call = await _call(db_session, user_id=active_user.id)
    service = RecordingLifecycleService(
        db_session,
        now_provider=lambda: FIXED_NOW,
    )
    operation = await service.prepare_start(call)
    assert await service.begin_start(operation.id) is not None

    await service.record_start_error(
        operation.id,
        outcome="unknown",
        error_code=error_code,
    )

    assert operation.start_state == "uncertain"
    assert operation.last_error_code == error_code


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("outcome", "error_code"),
    [
        ("unknown", "provider_exception: credential=secret"),
        ("maybe", "unknown"),
    ],
)
async def test_start_error_rejects_unbounded_values_before_mutation(
    db_session: AsyncSession,
    active_user,
    outcome: str,
    error_code: str,
) -> None:
    call = await _call(db_session, user_id=active_user.id)
    service = RecordingLifecycleService(
        db_session,
        now_provider=lambda: FIXED_NOW,
    )
    operation = await service.prepare_start(call)
    assert await service.begin_start(operation.id) is not None

    with pytest.raises(ValueError, match="Recording start"):
        await service.record_start_error(
            operation.id,
            outcome=outcome,  # type: ignore[arg-type]
            error_code=error_code,
        )

    assert operation.start_state == "starting"
    assert operation.last_error_code is None


@pytest.mark.anyio
async def test_call_end_requests_stop_without_provider_identity_and_accelerates(
    db_session: AsyncSession,
    active_user,
) -> None:
    call = await _call(db_session, user_id=active_user.id)
    current_time = [FIXED_NOW]
    service = RecordingLifecycleService(
        db_session,
        now_provider=lambda: current_time[0],
    )
    operation = await service.prepare_start(call)
    stop_requested_at = datetime(2026, 7, 19, 12, 1, tzinfo=UTC)
    current_time[0] = stop_requested_at
    call.status = "completed"
    await db_session.flush()

    stopped = await service.request_stop(call)

    assert stopped is operation
    assert operation.provider_egress_id is None
    assert _as_utc(operation.stop_requested_at) == stop_requested_at
    assert operation.delete_requested_at is None
    events = list(
        (
            await db_session.scalars(
                select(OutboxEvent)
                .where(
                    OutboxEvent.aggregate_type == RECORDING_AGGREGATE_TYPE,
                    OutboxEvent.aggregate_id == operation.id,
                )
                .order_by(OutboxEvent.created_at, OutboxEvent.id)
            )
        ).all()
    )
    assert {event.idempotency_key for event in events} == {
        f"recording.reconcile:{operation.id}:start",
        f"recording.reconcile:{operation.id}:stop",
    }
    assert all(event.payload == {"operation_id": str(operation.id)} for event in events)
    start_event = next(
        event for event in events if event.idempotency_key.endswith(":start")
    )
    assert _as_utc(start_event.next_attempt_at) == stop_requested_at


@pytest.mark.anyio
async def test_repeated_stop_and_delete_preserve_first_timestamps_and_events(
    db_session: AsyncSession,
    active_user,
) -> None:
    call = await _call(db_session, user_id=active_user.id)
    current_time = [FIXED_NOW]
    service = RecordingLifecycleService(
        db_session,
        now_provider=lambda: current_time[0],
    )
    operation = await service.prepare_start(call)
    call.status = "completed"
    first_stop = datetime(2026, 7, 19, 12, 1, tzinfo=UTC)
    first_delete = datetime(2026, 7, 19, 12, 2, tzinfo=UTC)
    current_time[0] = first_stop
    await service.request_stop(call)
    current_time[0] = first_delete
    await service.request_deletion(call)
    first_events = {
        event.idempotency_key: event.id
        for event in (
            await db_session.scalars(
                select(OutboxEvent).where(
                    OutboxEvent.aggregate_type == RECORDING_AGGREGATE_TYPE,
                    OutboxEvent.aggregate_id == operation.id,
                )
            )
        ).all()
    }

    current_time[0] = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)
    await service.request_stop(call)
    await service.request_deletion(call)
    repeated_events = {
        event.idempotency_key: event.id
        for event in (
            await db_session.scalars(
                select(OutboxEvent).where(
                    OutboxEvent.aggregate_type == RECORDING_AGGREGATE_TYPE,
                    OutboxEvent.aggregate_id == operation.id,
                )
            )
        ).all()
    }

    assert _as_utc(operation.stop_requested_at) == first_stop
    assert _as_utc(operation.delete_requested_at) == first_delete
    assert repeated_events == first_events
    assert set(repeated_events) == {
        f"recording.reconcile:{operation.id}:start",
        f"recording.reconcile:{operation.id}:stop",
        f"recording.reconcile:{operation.id}:delete",
    }


@pytest.mark.anyio
async def test_deletion_without_prior_stop_sets_both_intents_and_accelerates(
    db_session: AsyncSession,
    active_user,
) -> None:
    call = await _call(db_session, user_id=active_user.id)
    current_time = [FIXED_NOW]
    service = RecordingLifecycleService(
        db_session,
        now_provider=lambda: current_time[0],
    )
    operation = await service.prepare_start(call)
    call.status = "completed"
    delete_requested_at = datetime(2026, 7, 19, 12, 1, tzinfo=UTC)
    current_time[0] = delete_requested_at

    deleted = await service.request_deletion(call)

    assert deleted is operation
    assert _as_utc(operation.stop_requested_at) == delete_requested_at
    assert _as_utc(operation.delete_requested_at) == delete_requested_at
    events = list(
        (
            await db_session.scalars(
                select(OutboxEvent).where(
                    OutboxEvent.aggregate_type == RECORDING_AGGREGATE_TYPE,
                    OutboxEvent.aggregate_id == operation.id,
                )
            )
        ).all()
    )
    assert {event.idempotency_key for event in events} == {
        f"recording.reconcile:{operation.id}:start",
        f"recording.reconcile:{operation.id}:delete",
    }
    start_event = next(
        event for event in events if event.idempotency_key.endswith(":start")
    )
    assert _as_utc(start_event.next_attempt_at) == delete_requested_at


@pytest.mark.anyio
async def test_request_stop_relocks_call_before_operation_for_postgresql(
    db_session: AsyncSession,
    active_user,
) -> None:
    call = await _call(db_session, user_id=active_user.id)
    operation = await RecordingLifecycleService(
        db_session,
        now_provider=lambda: FIXED_NOW,
    ).prepare_start(call)
    call.status = "completed"
    await db_session.commit()
    statements = []

    def capture_statement(execute_state) -> None:
        if execute_state.is_select:
            statements.append(execute_state.statement)

    sqlalchemy_event.listen(
        db_session.sync_session, "do_orm_execute", capture_statement
    )
    try:
        stopped = await RecordingLifecycleService(
            db_session,
            now_provider=lambda: FIXED_NOW,
        ).request_stop(call)
    finally:
        sqlalchemy_event.remove(
            db_session.sync_session,
            "do_orm_execute",
            capture_statement,
        )

    assert stopped is not None
    assert stopped.id == operation.id
    compiled = [str(item.compile(dialect=postgresql.dialect())) for item in statements]
    call_lock_index = next(
        index
        for index, sql in enumerate(compiled)
        if "FROM calls" in sql and "FOR UPDATE" in sql
    )
    operation_lock_index = next(
        index
        for index, sql in enumerate(compiled)
        if "FROM recording_egress_operations" in sql and "FOR UPDATE" in sql
    )
    assert call_lock_index < operation_lock_index


@pytest.mark.anyio
async def test_deletion_repairs_known_legacy_recording_identity(
    db_session: AsyncSession,
    active_user,
) -> None:
    call = await _call(
        db_session,
        user_id=active_user.id,
        status="completed",
        room_name="legacy-room",
    )
    call.recording_object_key = "calls/legacy/exact.ogg"
    call.recording_egress_id = "EG_legacy"
    call.recording_url = "s3://legacy/private.ogg"
    await db_session.flush()

    operation = await RecordingLifecycleService(
        db_session,
        now_provider=lambda: FIXED_NOW,
    ).request_deletion(call)

    assert operation is not None
    assert operation.call_id == call.id
    assert operation.room_name == "legacy-room"
    assert operation.legacy_incomplete is False
    assert operation.expected_object_key == "calls/legacy/exact.ogg"
    assert operation.start_state == "started"
    assert operation.provider_egress_id == "EG_legacy"
    assert _as_utc(operation.stop_requested_at) == FIXED_NOW
    assert _as_utc(operation.delete_requested_at) == FIXED_NOW
    event = await db_session.scalar(
        select(OutboxEvent).where(
            OutboxEvent.aggregate_id == operation.id,
            OutboxEvent.idempotency_key.endswith(":delete"),
        )
    )
    assert event is not None
    assert event.payload == {"operation_id": str(operation.id)}


@pytest.mark.anyio
async def test_legacy_metadata_without_egress_identity_remains_uncertain(
    db_session: AsyncSession,
    active_user,
) -> None:
    call = await _call(
        db_session,
        user_id=active_user.id,
        status="failed",
        room_name="legacy-room",
    )
    call.started_at = None
    call.recording_object_key = "calls/legacy/maybe-started.ogg"
    await db_session.flush()

    operation = await RecordingLifecycleService(
        db_session,
        now_provider=lambda: FIXED_NOW,
    ).request_deletion(call)

    assert operation is not None
    assert operation.start_state == "uncertain"
    assert operation.provider_egress_id is None
    assert operation.legacy_incomplete is False


@pytest.mark.anyio
async def test_missing_legacy_room_is_incomplete_only_when_metadata_exists(
    db_session: AsyncSession,
    active_user,
) -> None:
    legacy_call = await _call(
        db_session,
        user_id=active_user.id,
        status="completed",
        room_name=None,
    )
    legacy_call.recording_url = "s3://legacy/path-without-room.ogg"
    await db_session.flush()

    legacy_operation = await RecordingLifecycleService(
        db_session,
        now_provider=lambda: FIXED_NOW,
    ).request_deletion(legacy_call)

    assert legacy_operation is not None
    assert legacy_operation.room_name is None
    assert legacy_operation.legacy_incomplete is True
    assert legacy_operation.start_state == "uncertain"
    assert legacy_operation.expected_object_key == build_recording_object_key(
        user_id=legacy_call.user_id,
        call_id=legacy_call.id,
    )

    no_metadata_call = await _call(
        db_session,
        user_id=active_user.id,
        status="completed",
        room_name=None,
    )
    absent = await RecordingLifecycleService(
        db_session,
        now_provider=lambda: FIXED_NOW,
    ).request_deletion(no_metadata_call)

    assert absent is None
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(RecordingEgressOperation)
            .where(RecordingEgressOperation.call_id == no_metadata_call.id)
        )
        == 0
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "field_name",
    ["recording_object_key", "recording_egress_id", "recording_url"],
)
async def test_empty_legacy_recording_values_count_as_absent(
    db_session: AsyncSession,
    active_user,
    field_name: str,
) -> None:
    call = await _call(
        db_session,
        user_id=active_user.id,
        status="completed",
        room_name=None,
    )
    setattr(call, field_name, "")
    await db_session.flush()

    operation = await RecordingLifecycleService(
        db_session,
        now_provider=lambda: FIXED_NOW,
    ).request_deletion(call)

    assert operation is None
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(RecordingEgressOperation)
            .where(RecordingEgressOperation.call_id == call.id)
        )
        == 0
    )


@pytest.mark.anyio
async def test_every_lifecycle_event_payload_is_operation_reference_only(
    db_session: AsyncSession,
    active_user,
) -> None:
    normal_call = await _call(db_session, user_id=active_user.id)
    service = RecordingLifecycleService(
        db_session,
        now_provider=lambda: FIXED_NOW,
    )
    normal_operation = await service.prepare_start(normal_call)
    normal_call.status = "completed"
    await service.request_stop(normal_call)
    await service.request_deletion(normal_call)

    legacy_call = await _call(
        db_session,
        user_id=active_user.id,
        status="completed",
        room_name="legacy-private-room",
    )
    legacy_call.recording_object_key = "calls/legacy/forbidden-in-payload.ogg"
    legacy_call.recording_egress_id = "EG_forbidden_in_payload"
    legacy_call.recording_url = "s3://forbidden-in-payload"
    await db_session.flush()
    legacy_operation = await service.request_deletion(legacy_call)
    assert legacy_operation is not None

    events = list(
        (
            await db_session.scalars(
                select(OutboxEvent).where(
                    OutboxEvent.aggregate_id.in_(
                        (normal_operation.id, legacy_operation.id)
                    )
                )
            )
        ).all()
    )
    assert len(events) == 4
    assert all(event.topic == "recording.reconcile" for event in events)
    assert all(set(event.payload) == {"operation_id"} for event in events)
    assert all(
        event.payload == {"operation_id": str(event.aggregate_id)} for event in events
    )
