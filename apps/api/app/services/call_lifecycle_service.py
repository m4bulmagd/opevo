import logging
from dataclasses import dataclass
from uuid import UUID

from app.core.logging import report_safe_exception
from app.repositories.call_repository import CallRepository
from app.repositories.message_repository import MessageRepository
from app.repositories.phone_number_repository import PhoneNumberRepository
from app.services.notification_service import NotificationService
from app.services.recording_service import RecordingResult, RecordingService
from app.services.summary_service import SummaryService
from app.services.telephony_service import TelephonyService
from app.services.transcript_service import TranscriptService
from app.services.usage_accounting_service import UsageAccountingService


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
        usage_accounting_service: UsageAccountingService,
        phone_number_repository: PhoneNumberRepository,
        telephony_service: TelephonyService,
        summary_service: SummaryService,
        recording_service: RecordingService,
        notification_service: NotificationService,
    ) -> None:
        self.session = session
        self.call_repository = call_repository
        self.message_repository = message_repository
        self.usage_accounting_service = usage_accounting_service
        self.phone_number_repository = phone_number_repository
        self.telephony_service = telephony_service
        self.summary_service = summary_service
        self.recording_service = recording_service
        self.notification_service = notification_service

    async def finalize_call(self, payload: dict) -> CallFinalizationResult:
        call_id = UUID(payload["call_id"])
        duration_seconds = payload["duration_seconds"]
        transcript_service = TranscriptService(
            self.session,
            call_repository=self.call_repository,
            message_repository=self.message_repository,
        )
        recovery = payload.get("transcript") or []
        if recovery:
            await transcript_service.merge_recovery(
                call_id=call_id,
                transcript=recovery,
            )

        debit = await self.usage_accounting_service.debit_call(
            call_id=call_id,
            duration_seconds=duration_seconds,
        )
        call = await self.call_repository.get_by_id(call_id)
        if call is None:
            raise ValueError("Call not found")
        if debit.already_debited:
            await self.session.commit()
            return CallFinalizationResult(
                minutes_charged=debit.minutes_charged,
                summary_job_enqueued=False,
                recording_job_enqueued=False,
                notification_job_enqueued=False,
                number_disabled=False,
                summary_text=call.summary_text,
                recording_key=None,
                already_completed=True,
            )

        messages = await self.message_repository.list_by_call_id(call.id)
        complete_transcript = [
            {
                "sequence_number": message.sequence_number,
                "speaker": message.speaker,
                "text": message.text,
            }
            for message in messages
        ]
        internal_payload = {
            **payload,
            "user_id": debit.user_id,
            "transcript": complete_transcript,
        }
        summary_result = await self.summary_service.create_summary(internal_payload)

        try:
            recording_result = await self.recording_service.store_recording(
                internal_payload
            )
        except Exception as exc:
            report_safe_exception(
                logger,
                event="call_recording_upload_failed",
                operation="store_recording",
                error=exc,
                call_id=call_id,
                user_id=debit.user_id,
                status="failed",
            )
            recording_result = RecordingResult(object_key=None, url=None, job_enqueued=False)

        await self.call_repository.mark_completed(
            call,
            duration_seconds=duration_seconds,
            minutes_charged=debit.minutes_charged,
            summary_text=summary_result.text,
            summary_data=summary_result.data,
            recording_object_key=recording_result.object_key,
            recording_url=recording_result.url,
        )

        notification_result = await self.notification_service.create_call_completed_notification(
            user_id=debit.user_id,
            call_id=call.id,
            summary_text=summary_result.text,
            minutes_charged=debit.minutes_charged,
        )

        number_disabled = debit.balance_after == 0
        if number_disabled:
            phone_number = await self.phone_number_repository.get_by_user_id(
                debit.user_id
            )
            if phone_number is not None:
                try:
                    await self.telephony_service.disable_number(debit.user_id)
                except Exception as exc:
                    report_safe_exception(
                        logger,
                        event="phone_number_disable_failed",
                        operation="disable_phone_number",
                        error=exc,
                        call_id=call_id,
                        user_id=debit.user_id,
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
            minutes_charged=debit.minutes_charged,
            summary_job_enqueued=summary_result.job_enqueued,
            recording_job_enqueued=recording_result.job_enqueued,
            notification_job_enqueued=notification_result.job_enqueued,
            number_disabled=number_disabled,
            summary_text=summary_result.text,
            recording_key=recording_result.object_key,
        )
