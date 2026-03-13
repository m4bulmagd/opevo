from dataclasses import dataclass

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


class CallLifecycleService:
    def __init__(self, session) -> None:
        self.session = session
        self.usage_repository = UsageRepository(session)
        self.phone_number_repository = PhoneNumberRepository(session)
        self.telephony_service = TelephonyService(session)
        self.summary_service = SummaryService()
        self.recording_service = RecordingService()
        self.notification_service = NotificationService()

    async def finalize_call(self, payload: dict) -> CallFinalizationResult:
        duration_seconds = payload["duration_seconds"]
        minutes_charged = max(1, (duration_seconds + 59) // 60)
        minutes_remaining = payload["minutes_remaining"]
        balance_after = max(0, minutes_remaining - minutes_charged)

        await self.usage_repository.create(
            user_id=payload["user_id"],
            event_type="call_completed",
            minutes_delta=-minutes_charged,
            balance_after=balance_after,
        )

        number_disabled = balance_after == 0
        if number_disabled:
            phone_number = await self.phone_number_repository.get_by_user_id(payload["user_id"])
            if phone_number is not None:
                await self.telephony_service.disable_number(payload["user_id"])

        await self.session.commit()

        return CallFinalizationResult(
            minutes_charged=minutes_charged,
            summary_job_enqueued=self.summary_service.enqueue(payload),
            recording_job_enqueued=self.recording_service.enqueue(payload),
            notification_job_enqueued=self.notification_service.enqueue(payload),
            number_disabled=number_disabled,
        )
