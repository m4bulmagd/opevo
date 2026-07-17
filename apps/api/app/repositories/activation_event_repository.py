from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.activation_event import ActivationEvent


class ActivationEventRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def append(
        self,
        *,
        user_id: UUID,
        activation_id: UUID,
        event_type: str,
        idempotency_key: str,
        metadata: dict,
    ) -> ActivationEvent:
        result = await self.session.execute(
            select(ActivationEvent).where(
                ActivationEvent.idempotency_key == idempotency_key
            )
        )
        event = result.scalar_one_or_none()
        if event is not None:
            return event

        event = ActivationEvent(
            user_id=user_id,
            activation_id=activation_id,
            event_type=event_type,
            idempotency_key=idempotency_key,
            event_metadata=metadata,
        )
        self.session.add(event)
        await self.session.flush()
        return event
