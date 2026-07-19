from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.call import Call
from app.repositories.call_repository import CallRepository
from app.repositories.message_repository import MessageRepository
from app.schemas.call_summary_projection import CallSummaryProjection
from app.schemas.calls import (
    CallDetailResponse,
    CallHistoryListItem,
    CallTranscriptLineResponse,
)
from app.services.recording_lifecycle_service import RecordingLifecycleService
from app.services.recording_service import RecordingService


class CallHistoryNotFoundError(Exception):
    pass


class CallDeleteActiveError(Exception):
    pass


SUMMARY_PROCESSING_STATES = frozenset({"pending", "connected", "ending", "finalizing"})
CALL_DELETE_TERMINAL_STATES = frozenset({"completed", "failed"})


class CallHistoryService:
    def __init__(
        self,
        session: AsyncSession,
        recording_service: RecordingService | None,
        recording_lifecycle_service: RecordingLifecycleService | None = None,
    ) -> None:
        self.session = session
        self.call_repository = CallRepository(session)
        self.message_repository = MessageRepository(session)
        self.recording_service = recording_service
        self.recording_lifecycle_service = (
            recording_lifecycle_service or RecordingLifecycleService(session)
        )

    async def list_calls(
        self, user_id: UUID, *, limit: int = 100, offset: int = 0
    ) -> list[CallHistoryListItem]:
        calls = await self.call_repository.list_visible_by_user_id(
            user_id, limit=limit, offset=offset
        )
        return [self._list_item(call) for call in calls]

    @staticmethod
    def _summary_fields(call: Call) -> tuple[str, CallSummaryProjection | None]:
        projection = CallSummaryProjection.from_stored(call.summary_data)
        if projection is not None or call.summary_text:
            return "ready", projection
        if call.status in SUMMARY_PROCESSING_STATES:
            return "processing", None
        return "unavailable", None

    def _list_item(self, call: Call) -> CallHistoryListItem:
        summary_status, projection = self._summary_fields(call)
        return CallHistoryListItem(
            id=call.id,
            status=call.status,
            caller_number=call.caller_number,
            started_at=call.started_at,
            ended_at=call.ended_at,
            duration_seconds=call.duration_seconds,
            minutes_charged=call.minutes_charged,
            summary_text=call.summary_text,
            has_recording=bool(call.recording_object_key),
            summary_status=summary_status,
            caller_intent=projection.caller_intent if projection else None,
            action_items=projection.action_items if projection else None,
            sentiment=projection.sentiment if projection else None,
            follow_up_required=(
                projection.follow_up_required if projection else None
            ),
        )

    async def get_call_detail(self, user_id: UUID, call_id: UUID) -> CallDetailResponse:
        call = await self.call_repository.get_visible_by_id(call_id, user_id=user_id)
        if call is None:
            raise CallHistoryNotFoundError
        if self.recording_service is None:
            raise RuntimeError("Recording playback capability is unavailable")

        messages = await self.message_repository.list_by_call_id(call.id)
        try:
            recording_url = await self.recording_service.get_access_url(
                call_id=call.id,
                user_id=user_id,
                recording_object_key=call.recording_object_key,
            )
        except FileNotFoundError:
            recording_url = None
        summary_status, projection = self._summary_fields(call)
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
            summary_status=summary_status,
            caller_intent=projection.caller_intent if projection else None,
            action_items=projection.action_items if projection else None,
            sentiment=projection.sentiment if projection else None,
            follow_up_required=(
                projection.follow_up_required if projection else None
            ),
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

    async def delete_call(self, user_id: UUID, call_id: UUID) -> None:
        try:
            call = (
                await self.call_repository.get_by_id_for_user_including_deleted_for_update(
                    call_id,
                    user_id=user_id,
                )
            )
            if call is None:
                raise CallHistoryNotFoundError
            if call.deleted_at is not None:
                await self.recording_lifecycle_service.request_deletion(call)
                await self.session.commit()
                return
            if call.status not in CALL_DELETE_TERMINAL_STATES:
                raise CallDeleteActiveError

            await self.recording_lifecycle_service.request_deletion(call)
            await self.message_repository.delete_by_call_id(call.id)
            await self.call_repository.purge_customer_content(call)
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            raise
