from uuid import uuid4

import pytest

from app.providers.notifications.firebase import FirebaseNotificationProvider
from app.services.notification_service import NotificationService


class ExplodingNotificationProvider:
    async def send_notification(self, **_kwargs) -> str:
        raise RuntimeError(
            "provider secret +33123456789 transcript recording customer"
        )


class CapturingNotificationRepository:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return object()


@pytest.mark.anyio
async def test_firebase_push_is_disabled_without_constructing_topic_message() -> None:
    provider = FirebaseNotificationProvider()

    status = await provider.send_notification(
        user_id="predictable-user-id",
        notification_type="call_completed",
        payload={
            "summary_text": "sensitive summary",
            "caller_number": "+33123456789",
            "transcript": "sensitive transcript",
            "recording_url": "https://storage.invalid/sensitive",
            "customer_identity": "sensitive customer",
        },
    )

    assert status == "disabled"
    assert not hasattr(provider, "_get_messaging")


@pytest.mark.anyio
async def test_notification_service_persists_only_opaque_dashboard_reference() -> None:
    repository = CapturingNotificationRepository()
    service = NotificationService(
        provider=ExplodingNotificationProvider(),
        notification_repository=repository,
    )
    user_id = uuid4()
    call_id = uuid4()

    result = await service.create_call_completed_notification(
        user_id=user_id,
        call_id=call_id,
        summary_text="sensitive summary",
        minutes_charged=42,
    )

    assert result.status == "disabled"
    assert result.job_enqueued is False
    assert repository.calls == [
        {
            "user_id": user_id,
            "call_id": call_id,
            "notification_type": "call_completed",
            "status": "disabled",
            "payload": {"event": "call_completed", "call_id": str(call_id)},
        }
    ]
    persisted = str(repository.calls)
    assert "sensitive summary" not in persisted
    assert "+33123456789" not in persisted
    assert "provider secret" not in persisted
