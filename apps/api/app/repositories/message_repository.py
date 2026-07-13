from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.call_message import CallMessage


class MessageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_sequence(
        self,
        *,
        call_id: UUID,
        sequence_number: int,
    ) -> CallMessage | None:
        result = await self.session.execute(
            select(CallMessage).where(
                CallMessage.call_id == call_id,
                CallMessage.sequence_number == sequence_number,
            )
        )
        return result.scalar_one_or_none()

    async def insert_with_unique_backstop(
        self,
        *,
        call_id: UUID,
        sequence_number: int,
        speaker: str,
        text: str,
    ) -> tuple[CallMessage, bool]:
        message = CallMessage(
            call_id=call_id,
            sequence_number=sequence_number,
            speaker=speaker,
            text=text,
        )
        try:
            async with self.session.begin_nested():
                self.session.add(message)
                await self.session.flush()
        except IntegrityError:
            existing = await self.get_by_sequence(
                call_id=call_id,
                sequence_number=sequence_number,
            )
            if existing is None:
                raise
            return existing, False
        return message, True

    async def list_by_call_id(self, call_id: UUID) -> list[CallMessage]:
        result = await self.session.execute(
            select(CallMessage)
            .where(CallMessage.call_id == call_id)
            .order_by(CallMessage.sequence_number.asc(), CallMessage.created_at.asc())
        )
        return list(result.scalars())

    async def max_sequence_by_call_id(self, call_id: UUID) -> int:
        value = await self.session.scalar(
            select(func.max(CallMessage.sequence_number)).where(
                CallMessage.call_id == call_id
            )
        )
        return int(value or 0)
