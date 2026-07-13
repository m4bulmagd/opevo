from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
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

    async def get_or_create(
        self,
        *,
        user_id: UUID,
        call_id: UUID,
        notification_type: str,
        status: str,
        payload: dict,
    ) -> tuple[Notification, bool]:
        dialect_name = self.session.get_bind().dialect.name
        insert = sqlite_insert if dialect_name == "sqlite" else postgresql_insert
        statement = (
            insert(Notification)
            .values(
                user_id=user_id,
                call_id=call_id,
                notification_type=notification_type,
                status=status,
                payload=payload,
            )
            .on_conflict_do_nothing(
                index_elements=["call_id", "notification_type"]
            )
            .returning(Notification.id)
        )
        inserted_id = await self.session.scalar(statement)
        notification = await self.session.scalar(
            select(Notification).where(
                Notification.call_id == call_id,
                Notification.notification_type == notification_type,
            )
        )
        if notification is None:
            raise RuntimeError("Notification identity row could not be loaded")
        if notification.user_id != user_id or notification.payload != payload:
            raise ValueError("Notification identity belongs to different content")
        return notification, inserted_id is not None
