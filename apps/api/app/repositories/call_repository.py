from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.call import Call


class CallRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(self, call_id: UUID) -> Call | None:
        return await self.session.get(Call, call_id)

    async def create_pending(
        self,
        *,
        user_id: UUID,
        phone_number_id: UUID | None = None,
        livekit_room_id: str | None = None,
        caller_number: str | None = None,
    ) -> Call:
        call = Call(
            user_id=user_id,
            phone_number_id=phone_number_id,
            livekit_room_id=livekit_room_id,
            caller_number=caller_number,
            status="pending",
        )
        self.session.add(call)
        await self.session.flush()
        return call

    async def mark_completed(
        self,
        call: Call,
        *,
        duration_seconds: int,
        minutes_charged: int,
        summary_text: str | None,
        recording_url: str | None,
    ) -> Call:
        call.status = "completed"
        call.ended_at = datetime.now(timezone.utc)
        call.duration_seconds = duration_seconds
        call.minutes_charged = minutes_charged
        call.summary_text = summary_text
        call.recording_url = recording_url
        await self.session.flush()
        return call
