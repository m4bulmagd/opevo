from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import Notification


class NotificationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        user_id: UUID,
        call_id: UUID | None,
        notification_type: str,
        status: str,
        payload: dict,
    ) -> Notification:
        notification = Notification(
            user_id=user_id,
            call_id=call_id,
            notification_type=notification_type,
            status=status,
            payload=payload,
        )
        self.session.add(notification)
        await self.session.flush()
        return notification
