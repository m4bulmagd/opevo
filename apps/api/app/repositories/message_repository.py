from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.call_message import CallMessage


class MessageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_many(self, *, call_id: UUID, transcript: list[dict]) -> list[CallMessage]:
        messages: list[CallMessage] = []
        for index, line in enumerate(transcript, start=1):
            text = line.get("text", "").strip()
            if not text:
                continue
            message = CallMessage(
                call_id=call_id,
                speaker=line.get("speaker", "UNKNOWN"),
                text=text,
                sequence_number=index,
            )
            self.session.add(message)
            messages.append(message)
        await self.session.flush()
        return messages

    async def list_by_call_id(self, call_id: UUID) -> list[CallMessage]:
        result = await self.session.execute(
            select(CallMessage)
            .where(CallMessage.call_id == call_id)
            .order_by(CallMessage.sequence_number.asc(), CallMessage.created_at.asc())
        )
        return list(result.scalars())
