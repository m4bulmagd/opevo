import logging
from dataclasses import dataclass
from uuid import UUID

from app.core.logging import report_safe_exception
from app.repositories.call_repository import CallRepository
from app.repositories.message_repository import MessageRepository
from app.repositories.phone_number_repository import PhoneNumberRepository
from app.repositories.usage_repository import UsageRepository
from app.services.notification_service import NotificationService
from app.services.recording_service import RecordingResult, RecordingService
from app.services.summary_service import SummaryService
from app.services.telephony_service import TelephonyService


logger = logging.getLogger(__name__)


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
        call_repository: CallRepository,
        message_repository: MessageRepository,
        usage_repository: UsageRepository,
        phone_number_repository: PhoneNumberRepository,
        telephony_service: TelephonyService,
        summary_service: SummaryService,
        recording_service: RecordingService,
        notification_service: NotificationService,
    ) -> None:
        self.session = session
        self.call_repository = call_repository
        self.message_repository = message_repository
        self.usage_repository = usage_repository
        self.phone_number_repository = phone_number_repository
        self.telephony_service = telephony_service
        self.summary_service = summary_service
        self.recording_service = recording_service
        self.notification_service = notification_service

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

        try:
            recording_result = await self.recording_service.store_recording(payload)
        except Exception as exc:
            report_safe_exception(
                logger,
                event="call_recording_upload_failed",
                operation="store_recording",
                error=exc,
                call_id=call_id,
                user_id=payload.get("user_id"),
                status="failed",
            )
            recording_result = RecordingResult(object_key=None, url=None, job_enqueued=False)

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
            recording_object_key=recording_result.object_key,
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
                try:
                    await self.telephony_service.disable_number(payload["user_id"])
                except Exception as exc:
                    report_safe_exception(
                        logger,
                        event="phone_number_disable_failed",
                        operation="disable_phone_number",
                        error=exc,
                        call_id=call_id,
                        user_id=payload.get("user_id"),
                        provider_request_id=getattr(
                            phone_number,
                            "provider_number_id",
                            None,
                        ),
                        status="failed",
                    )
                    number_disabled = False

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
