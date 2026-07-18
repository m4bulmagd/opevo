from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.outbox_event import OutboxEvent
from app.services.outbox_service import OutboxService


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
