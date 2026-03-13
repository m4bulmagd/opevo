from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.call import Call


class CallRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

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
