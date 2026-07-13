from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.outbox_event import OutboxEvent
from app.repositories.outbox_repository import OutboxRepository


class OutboxService:
    def __init__(self, session: AsyncSession) -> None:
        self.repository = OutboxRepository(session)

    async def add(
        self,
        *,
        topic: str,
        aggregate_type: str,
        aggregate_id: UUID,
        idempotency_key: str,
        payload: dict,
    ) -> OutboxEvent:
        return await self.repository.add(
            topic=topic,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            idempotency_key=idempotency_key,
            payload=payload,
            next_attempt_at=datetime.now(UTC),
        )
