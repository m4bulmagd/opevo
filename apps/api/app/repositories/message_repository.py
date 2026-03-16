from uuid import UUID

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
