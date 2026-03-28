from dataclasses import dataclass
from uuid import UUID

from app.repositories.call_repository import CallRepository
from app.repositories.message_repository import MessageRepository
from app.repositories.phone_number_repository import PhoneNumberRepository
from app.repositories.usage_repository import UsageRepository
from app.services.notification_service import NotificationService
from app.services.recording_service import RecordingService
from app.services.summary_service import SummaryService
from app.services.telephony_service import TelephonyService


@dataclass
class CallFinalizationResult:
    minutes_charged: int
    summary_job_enqueued: bool
    recording_job_enqueued: bool
    notification_job_enqueued: bool
    number_disabled: bool
    summary_text: str | None
    recording_key: str | None
    already_completed: bool = False


class CallLifecycleService:
    def __init__(
        self,
        session,
        *,
        telephony_service: TelephonyService | None = None,
        summary_service: SummaryService | None = None,
        recording_service: RecordingService | None = None,
        notification_service: NotificationService | None = None,
    ) -> None:
        self.session = session
        self.call_repository = CallRepository(session)
        self.message_repository = MessageRepository(session)
        self.usage_repository = UsageRepository(session)
        self.phone_number_repository = PhoneNumberRepository(session)
        self.telephony_service = telephony_service or TelephonyService(session)
        self.summary_service = summary_service or SummaryService()
        self.recording_service = recording_service or RecordingService()
        self.notification_service = notification_service or NotificationService(session)

    async def finalize_call(self, payload: dict) -> CallFinalizationResult:
        call_id = UUID(payload["call_id"])
        duration_seconds = payload["duration_seconds"]
        minutes_charged = max(1, (duration_seconds + 59) // 60)
        minutes_remaining = payload["minutes_remaining"]
        balance_after = max(0, minutes_remaining - minutes_charged)
        call = await self.call_repository.get_by_id(call_id)
        if call is None:
            raise ValueError("Call not found")
        if call.status == "completed":
            return CallFinalizationResult(
                minutes_charged=call.minutes_charged or 0,
                summary_job_enqueued=False,
                recording_job_enqueued=False,
                notification_job_enqueued=False,
                number_disabled=False,
                summary_text=call.summary_text,
                recording_key=None,
                already_completed=True,
            )

        summary_result = await self.summary_service.create_summary(payload)
        recording_result = await self.recording_service.store_recording(payload)
        await self.message_repository.create_many(
            call_id=call.id,
            transcript=payload.get("transcript") or [],
        )

        await self.call_repository.mark_completed(
            call,
            duration_seconds=duration_seconds,
            minutes_charged=minutes_charged,
            summary_text=summary_result.text,
            summary_data=summary_result.data,
            recording_url=recording_result.url,
        )

        await self.usage_repository.create(
            user_id=payload["user_id"],
            call_id=call.id,
            event_type="call_completed",
            minutes_delta=-minutes_charged,
            balance_after=balance_after,
        )

        notification_result = await self.notification_service.create_call_completed_notification(
            user_id=payload["user_id"],
            call_id=call.id,
            summary_text=summary_result.text,
            minutes_charged=minutes_charged,
        )

        number_disabled = balance_after == 0
        if number_disabled:
            phone_number = await self.phone_number_repository.get_by_user_id(payload["user_id"])
            if phone_number is not None:
                await self.telephony_service.disable_number(payload["user_id"])

        await self.session.commit()

        return CallFinalizationResult(
            minutes_charged=minutes_charged,
            summary_job_enqueued=summary_result.job_enqueued,
            recording_job_enqueued=recording_result.job_enqueued,
            notification_job_enqueued=notification_result.job_enqueued,
            number_disabled=number_disabled,
            summary_text=summary_result.text,
            recording_key=recording_result.object_key,
        )
