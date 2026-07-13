from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
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
async def test_duplicate_outbox_intent_is_rejected_by_the_unique_identity(
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

    await service.add(**arguments)
    await db_session.commit()

    with pytest.raises(IntegrityError):
        await service.add(**arguments)
    await db_session.rollback()

    assert await db_session.scalar(select(func.count()).select_from(OutboxEvent)) == 1


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
        "delivered_at",
        "created_at",
        "updated_at",
        "id",
    }
    assert columns.idempotency_key.nullable is False
    assert columns.payload.nullable is False
    assert columns.next_attempt_at.nullable is False
    assert columns.last_error_code.nullable is True
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
