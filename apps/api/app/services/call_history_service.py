from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.call_repository import CallRepository
from app.repositories.message_repository import MessageRepository
from app.repositories.user_repository import UserRepository
from app.schemas.calls import (
    CallDetailResponse,
    CallHistoryListItem,
    CallTranscriptLineResponse,
)
from app.services.recording_service import RecordingService


class CallHistoryNotFoundError(Exception):
    pass


class CallHistoryService:
    def __init__(
        self,
        session: AsyncSession,
        recording_service: RecordingService | None = None,
    ) -> None:
        self.session = session
        self.user_repository = UserRepository(session)
        self.call_repository = CallRepository(session)
        self.message_repository = MessageRepository(session)
        self.recording_service = recording_service or RecordingService()

    async def _get_user_id(self, clerk_user_id: str):
        user = await self.user_repository.get_by_clerk_user_id(clerk_user_id)
        if user is None:
            raise CallHistoryNotFoundError
        return user.id

    async def list_calls(self, clerk_user_id: str) -> list[CallHistoryListItem]:
        user_id = await self._get_user_id(clerk_user_id)
        calls = await self.call_repository.list_visible_by_user_id(user_id)
        return [
            CallHistoryListItem(
                id=call.id,
                status=call.status,
                caller_number=call.caller_number,
                started_at=call.started_at,
                ended_at=call.ended_at,
                duration_seconds=call.duration_seconds,
                minutes_charged=call.minutes_charged,
                summary_text=call.summary_text,
                has_recording=bool(call.recording_url),
            )
            for call in calls
        ]

    async def get_call_detail(self, clerk_user_id: str, call_id: UUID) -> CallDetailResponse:
        user_id = await self._get_user_id(clerk_user_id)
        call = await self.call_repository.get_visible_by_id(call_id, user_id=user_id)
        if call is None:
            raise CallHistoryNotFoundError

        messages = await self.message_repository.list_by_call_id(call.id)
        recording_url = await self.recording_service.get_access_url(
            call_id=call.id,
            user_id=user_id,
            stored_url=call.recording_url,
        )
        return CallDetailResponse(
            id=call.id,
            status=call.status,
            caller_number=call.caller_number,
            started_at=call.started_at,
            ended_at=call.ended_at,
            duration_seconds=call.duration_seconds,
            minutes_charged=call.minutes_charged,
            summary_text=call.summary_text,
            recording_url=recording_url,
            transcript=[
                CallTranscriptLineResponse(
                    speaker=message.speaker,
                    text=message.text,
                    sequence_number=message.sequence_number,
                    created_at=message.created_at,
                )
                for message in messages
            ],
        )

    async def delete_call(self, clerk_user_id: str, call_id: UUID) -> None:
        user_id = await self._get_user_id(clerk_user_id)
        call = await self.call_repository.get_visible_by_id(call_id, user_id=user_id)
        if call is None:
            raise CallHistoryNotFoundError

        await self.call_repository.soft_delete(call)
        await self.session.commit()
