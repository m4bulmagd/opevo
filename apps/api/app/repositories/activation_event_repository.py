from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
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
        event_id = uuid4()
        values = {
            "id": event_id,
            "user_id": user_id,
            "activation_id": activation_id,
            "event_type": event_type,
            "idempotency_key": idempotency_key,
            "event_metadata": metadata,
        }
        dialect_name = self.session.bind.dialect.name
        insert = sqlite_insert if dialect_name == "sqlite" else postgresql_insert
        statement = (
            insert(ActivationEvent)
            .values(**values)
            .on_conflict_do_nothing(index_elements=["idempotency_key"])
            .returning(ActivationEvent.id)
        )
        inserted_id = await self.session.scalar(statement)
        durable_id = inserted_id or await self.session.scalar(
            select(ActivationEvent.id).where(
                ActivationEvent.idempotency_key == idempotency_key
            )
        )
        if durable_id is None:
            raise RuntimeError("Activation event insert did not produce a durable row")
        event = await self.session.get(ActivationEvent, durable_id)
        if event is None:
            raise RuntimeError("Activation event row could not be loaded")
        return event
