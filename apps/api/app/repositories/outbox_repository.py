from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.outbox_event import OutboxEvent


class OutboxRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(
        self,
        *,
        topic: str,
        aggregate_type: str,
        aggregate_id: UUID,
        idempotency_key: str,
        payload: dict,
        next_attempt_at: datetime,
    ) -> OutboxEvent:
        event = OutboxEvent(
            topic=topic,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            idempotency_key=idempotency_key,
            payload=payload,
            next_attempt_at=next_attempt_at,
        )
        self.session.add(event)
        await self.session.flush()

        return event
