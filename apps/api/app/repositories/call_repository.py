from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.call import Call


class CallRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(self, call_id: UUID) -> Call | None:
        return await self.session.get(Call, call_id)

    async def list_visible_by_user_id(self, user_id: UUID) -> list[Call]:
        result = await self.session.execute(
            select(Call)
            .where(Call.user_id == user_id, Call.deleted_at.is_(None))
            .order_by(Call.started_at.desc().nullslast(), Call.created_at.desc())
        )
        return list(result.scalars())

    async def get_visible_by_id(self, call_id: UUID, *, user_id: UUID) -> Call | None:
        result = await self.session.execute(
            select(Call).where(
                Call.id == call_id,
                Call.user_id == user_id,
                Call.deleted_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

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

    async def get_pending_by_room_without_recording(self, *, room_name: str) -> Call | None:
        result = await self.session.execute(
            select(Call).where(
                Call.livekit_room_id == room_name,
                Call.status == "pending",
                Call.recording_egress_id.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def get_active_by_room_with_recording(self, *, room_name: str) -> Call | None:
        result = await self.session.execute(
            select(Call).where(
                Call.livekit_room_id == room_name,
                Call.recording_egress_id.is_not(None),
                Call.deleted_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def mark_completed(
        self,
        call: Call,
        *,
        duration_seconds: int,
        minutes_charged: int,
        summary_text: str | None,
        summary_data: dict | None,
        recording_object_key: str | None,
        recording_url: str | None,
    ) -> Call:
        call.status = "completed"
        call.ended_at = datetime.now(timezone.utc)
        call.duration_seconds = duration_seconds
        call.minutes_charged = minutes_charged
        call.summary_text = summary_text
        call.summary_data = summary_data
        if recording_object_key is not None:
            call.recording_object_key = recording_object_key
        if recording_url is not None:
            call.recording_url = recording_url
        await self.session.flush()
        return call

    async def set_recording_metadata(
        self,
        call: Call,
        *,
        recording_object_key: str,
        recording_egress_id: str,
        recording_url: str | None,
    ) -> Call:
        call.recording_object_key = recording_object_key
        call.recording_egress_id = recording_egress_id
        call.recording_url = recording_url
        await self.session.flush()
        return call

    async def soft_delete(self, call: Call) -> Call:
        call.deleted_at = datetime.now(timezone.utc)
        await self.session.flush()
        return call
