from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.outbox_event import OutboxEvent
from app.repositories.outbox_repository import OutboxRepository
from app.services.outbox_service import (
    OutboxPayloadError,
    OutboxService,
    validate_outbox_payload,
)


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _recording_event(
    *,
    operation_id: UUID,
    phase: str,
    created_at: datetime,
    next_attempt_at: datetime,
    status: str = "pending",
) -> OutboxEvent:
    return OutboxEvent(
        id=uuid4(),
        idempotency_key=f"recording.reconcile:{operation_id}:{phase}",
        topic="recording.reconcile",
        aggregate_type="recording-egress-operation",
        aggregate_id=operation_id,
        payload={"operation_id": str(operation_id)},
        status=status,
        attempt_count=1 if status == "processing" else 0,
        next_attempt_at=next_attempt_at,
        created_at=created_at,
    )


@pytest.mark.anyio
async def test_outbox_add_preserves_explicit_future_due_time(
    db_session: AsyncSession,
) -> None:
    operation_id = uuid4()
    due_at = datetime(2030, 1, 1, tzinfo=UTC)

    event = await OutboxService(db_session).add(
        topic="recording.reconcile",
        aggregate_type="recording-egress-operation",
        aggregate_id=operation_id,
        idempotency_key=f"recording.reconcile:{operation_id}:start",
        payload={"operation_id": str(operation_id)},
        next_attempt_at=due_at,
    )

    stored_due_at = event.next_attempt_at
    if stored_due_at.tzinfo is None:
        stored_due_at = stored_due_at.replace(tzinfo=UTC)
    assert stored_due_at == due_at


def test_recording_reconcile_payload_accepts_exact_operation_reference() -> None:
    validate_outbox_payload(
        "recording.reconcile",
        {"operation_id": str(uuid4())},
    )


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"operation_id": "not-a-uuid"},
        {"operation_id": str(uuid4()), "room_name": "private-room"},
    ],
)
def test_recording_reconcile_payload_rejects_invalid_references(
    payload: dict,
) -> None:
    with pytest.raises(OutboxPayloadError):
        validate_outbox_payload("recording.reconcile", payload)


@pytest.mark.anyio
async def test_acceleration_changes_only_oldest_pending_event(
    db_session: AsyncSession,
) -> None:
    operation_id = uuid4()
    now = datetime(2026, 7, 19, tzinfo=UTC)
    first = _recording_event(
        operation_id=operation_id,
        phase="start",
        created_at=now - timedelta(minutes=1),
        next_attempt_at=datetime(2030, 1, 1, tzinfo=UTC),
    )
    second = _recording_event(
        operation_id=operation_id,
        phase="stop",
        created_at=now,
        next_attempt_at=datetime(2030, 1, 2, tzinfo=UTC),
    )
    db_session.add_all([first, second])
    await db_session.flush()

    changed = await OutboxRepository(db_session).make_oldest_pending_due(
        aggregate_type="recording-egress-operation",
        aggregate_id=operation_id,
        due_at=now,
    )

    assert changed is True
    assert first.next_attempt_at == now
    assert second.next_attempt_at == datetime(2030, 1, 2, tzinfo=UTC)


@pytest.mark.anyio
async def test_acceleration_never_steals_processing_lease(
    db_session: AsyncSession,
) -> None:
    operation_id = uuid4()
    now = datetime(2026, 7, 19, tzinfo=UTC)
    first = _recording_event(
        operation_id=operation_id,
        phase="start",
        created_at=now - timedelta(minutes=1),
        next_attempt_at=datetime(2030, 1, 1, tzinfo=UTC),
        status="processing",
    )
    second = _recording_event(
        operation_id=operation_id,
        phase="stop",
        created_at=now,
        next_attempt_at=datetime(2030, 1, 2, tzinfo=UTC),
    )
    db_session.add_all([first, second])
    await db_session.flush()

    changed = await OutboxRepository(db_session).make_oldest_pending_due(
        aggregate_type="recording-egress-operation",
        aggregate_id=operation_id,
        due_at=now,
    )

    assert changed is True
    assert first.next_attempt_at == datetime(2030, 1, 1, tzinfo=UTC)
    assert second.next_attempt_at == now
    assert await OutboxRepository(db_session).claim_batch(limit=1, now=now) == []


@pytest.mark.anyio
async def test_injected_creation_clock_preserves_semantic_aggregate_order(
    db_session: AsyncSession,
) -> None:
    operation_id = uuid4()
    start_created_at = datetime(2026, 7, 19, 12, 0, tzinfo=UTC)
    stop_created_at = start_created_at + timedelta(minutes=1)
    creation_times = iter((start_created_at, stop_created_at))
    service = OutboxService(
        db_session,
        now_provider=lambda: next(creation_times),
    )
    first = await service.add(
        topic="recording.reconcile",
        aggregate_type="recording-egress-operation",
        aggregate_id=operation_id,
        idempotency_key=f"recording.reconcile:{operation_id}:start",
        payload={"operation_id": str(operation_id)},
        next_attempt_at=datetime(2030, 1, 1, tzinfo=UTC),
    )
    second = await service.add(
        topic="recording.reconcile",
        aggregate_type="recording-egress-operation",
        aggregate_id=operation_id,
        idempotency_key=f"recording.reconcile:{operation_id}:stop",
        payload={"operation_id": str(operation_id)},
        next_attempt_at=datetime(2030, 1, 2, tzinfo=UTC),
    )

    changed = await OutboxRepository(db_session).make_oldest_pending_due(
        aggregate_type="recording-egress-operation",
        aggregate_id=operation_id,
        due_at=stop_created_at,
    )

    assert changed is True
    assert _as_utc(first.created_at) == start_created_at
    assert _as_utc(second.created_at) == stop_created_at
    assert _as_utc(first.next_attempt_at) == stop_created_at
    assert _as_utc(second.next_attempt_at) == datetime(2030, 1, 2, tzinfo=UTC)


@pytest.mark.anyio
async def test_outbox_event_rolls_back_with_business_transaction(
    db_session: AsyncSession,
) -> None:
    await OutboxService(db_session).add(
        topic="phone.disable",
        aggregate_type="subscription",
        aggregate_id=uuid4(),
        idempotency_key="stripe:customer.subscription.updated:evt_rollback",
        payload={"user_id": str(uuid4())},
    )

    await db_session.rollback()

    assert await db_session.scalar(select(func.count()).select_from(OutboxEvent)) == 0


@pytest.mark.anyio
async def test_duplicate_outbox_intent_with_same_content_returns_existing_event(
    db_session: AsyncSession,
) -> None:
    service = OutboxService(db_session)
    aggregate_id = uuid4()
    arguments = {
        "topic": "phone.disable",
        "aggregate_type": "subscription",
        "aggregate_id": aggregate_id,
        "idempotency_key": "stripe:customer.subscription.updated:evt_duplicate",
        "payload": {"user_id": str(uuid4())},
    }

    first = await service.add(**arguments)
    await db_session.commit()

    second = await service.add(**arguments)
    await db_session.commit()

    assert second.id == first.id
    assert await db_session.scalar(select(func.count()).select_from(OutboxEvent)) == 1


@pytest.mark.anyio
async def test_duplicate_identity_with_different_content_is_controlled_conflict(
    db_session: AsyncSession,
) -> None:
    from app.services.outbox_service import OutboxIdempotencyConflictError

    service = OutboxService(db_session)
    aggregate_id = uuid4()
    await service.add(
        topic="phone.disable",
        aggregate_type="subscription",
        aggregate_id=aggregate_id,
        idempotency_key="same-key-different-content",
        payload={"user_id": str(uuid4())},
    )
    await db_session.commit()

    with pytest.raises(OutboxIdempotencyConflictError):
        await service.add(
            topic="phone.enable",
            aggregate_type="subscription",
            aggregate_id=aggregate_id,
            idempotency_key="same-key-different-content",
            payload={"user_id": str(uuid4())},
        )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("topic", "payload"),
    [
        ("phone.provision", {"user_id": str(uuid4()), "raw_body": "secret"}),
        ("phone.disable", {"user_id": str(uuid4()), "phone_number": "+33123456789"}),
        ("livekit.dispatch", {"call_id": str(uuid4()), "metadata": {"prompt": "secret"}}),
        ("recording.start", {"recording_bytes": b"audio"}),
        ("notification.send", {"notification_id": str(uuid4()), "summary_text": "secret"}),
    ],
)
async def test_outbox_rejects_non_reference_payloads(
    db_session: AsyncSession,
    topic: str,
    payload: dict,
) -> None:
    from app.services.outbox_service import OutboxPayloadError

    with pytest.raises(OutboxPayloadError):
        await OutboxService(db_session).add(
            topic=topic,
            aggregate_type="test",
            aggregate_id=uuid4(),
            idempotency_key=f"invalid:{uuid4().hex}",
            payload=payload,
        )


def test_outbox_model_exposes_the_task7_launch_shape() -> None:
    columns = OutboxEvent.__table__.columns

    assert set(columns.keys()) == {
        "idempotency_key",
        "topic",
        "aggregate_type",
        "aggregate_id",
        "payload",
        "status",
        "attempt_count",
        "next_attempt_at",
        "last_error_code",
        "routing_target_provider_number_id",
        "delivered_at",
        "created_at",
        "updated_at",
        "id",
    }
    assert columns.idempotency_key.nullable is False
    assert columns.payload.nullable is False
    assert columns.next_attempt_at.nullable is False
    assert columns.last_error_code.nullable is True
    assert columns.routing_target_provider_number_id.nullable is True
    assert columns.delivered_at.nullable is True
    assert columns.status.server_default is not None
    assert columns.attempt_count.server_default is not None
    assert columns.next_attempt_at.server_default is not None

    constraint_names = {
        constraint.name for constraint in OutboxEvent.__table__.constraints
    }
    assert "uq_outbox_events_idempotency_key" in constraint_names

    index_names = {index.name for index in OutboxEvent.__table__.indexes}
    assert {
        "ix_outbox_events_topic",
        "ix_outbox_events_aggregate_id",
        "ix_outbox_events_status",
    } <= index_names
