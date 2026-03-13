from dataclasses import dataclass
from uuid import UUID

from app.providers.notifications.base import NotificationProvider
from app.providers.notifications.firebase import FirebaseNotificationProvider
from app.repositories.notification_repository import NotificationRepository


@dataclass(frozen=True)
class NotificationResult:
    status: str
    job_enqueued: bool


class NotificationService:
    def __init__(self, session, provider: NotificationProvider | None = None) -> None:
        self.provider = provider or FirebaseNotificationProvider()
        self.notification_repository = NotificationRepository(session)

    async def create_call_completed_notification(
        self,
        *,
        user_id: UUID,
        call_id: UUID,
        summary_text: str | None,
        minutes_charged: int,
    ) -> NotificationResult:
        payload = {
            "event": "call_completed",
            "summary_text": summary_text,
            "minutes_charged": minutes_charged,
        }
        status = await self.provider.send_notification(
            user_id=user_id,
            notification_type="call_completed",
            payload=payload,
        )
        await self.notification_repository.create(
            user_id=user_id,
            call_id=call_id,
            notification_type="call_completed",
            status=status,
            payload=payload,
        )
        return NotificationResult(status=status, job_enqueued=True)
