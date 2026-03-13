from app.providers.notifications.base import NotificationProvider


class FirebaseNotificationProvider(NotificationProvider):
    async def send_notification(self, *, user_id, notification_type: str, payload: dict) -> str:
        return "sent"
