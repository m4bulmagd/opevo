import asyncio
import json

from app.core.config import get_settings
from app.providers.notifications.base import NotificationProvider


class FirebaseNotificationProvider(NotificationProvider):
    def __init__(self, *, messaging_module=None) -> None:
        self.messaging_module = messaging_module

    def _get_messaging(self):
        if self.messaging_module is not None:
            return self.messaging_module

        import firebase_admin
        from firebase_admin import credentials, messaging

        try:
            firebase_admin.get_app()
        except ValueError:
            settings = get_settings()
            if settings.firebase_credentials_json:
                certificate_payload = json.loads(settings.firebase_credentials_json)
                firebase_admin.initialize_app(credentials.Certificate(certificate_payload))
            else:
                firebase_admin.initialize_app()

        self.messaging_module = messaging
        return self.messaging_module

    async def send_notification(self, *, user_id, notification_type: str, payload: dict) -> str:
        messaging = self._get_messaging()
        summary_text = str(payload.get("summary_text") or notification_type.replace("_", " ").title())
        notification = messaging.Notification(
            title="AI Call Assistant",
            body=summary_text,
        )
        data = {"notification_type": notification_type}
        for key, value in payload.items():
            if value is not None:
                data[key] = str(value)
        message = messaging.Message(
            topic=f"user-{user_id}",
            data=data,
            notification=notification,
        )
        return await asyncio.to_thread(messaging.send, message)
