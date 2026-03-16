class NotificationProvider:
    async def send_notification(self, *, user_id, notification_type: str, payload: dict) -> str:
        raise NotImplementedError
