import pytest
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.call import Call
from app.models.call_message import CallMessage
from app.models.notification import Notification
from app.models.phone_number import PhoneNumber
from app.services.call_lifecycle_service import CallLifecycleService
from app.services.notification_service import NotificationService
from app.services.recording_service import RecordingService
from app.services.telephony_service import TelephonyService


class FakeTelephonyProvider:
    async def disable_number(self, *, provider_number_id: str) -> str:
        return "app-disabled"


class FakeStorageProvider:
    async def upload_bytes(self, *, object_key: str, data: bytes, content_type: str):
        class Stored:
            def __init__(self, object_key: str) -> None:
                self.object_key = object_key
                self.url = f"s3://recordings/{object_key}"

        return Stored(object_key)


class FakeNotificationProvider:
    async def send_notification(self, *, user_id, notification_type: str, payload: dict) -> str:
        return "sent"


class FakeFailingNotificationProvider:
    async def send_notification(self, *, user_id, notification_type: str, payload: dict) -> str:
        raise ValueError("messaging misconfigured")


class FakeStructuredSummaryService:
    async def create_summary(self, payload: dict):
        class Result:
            text = "Caller asked about opening hours."
            data = {
                "summary_text": "Caller asked about opening hours.",
                "caller_intent": "Ask about opening hours",
                "action_items": ["Provide opening hours"],
                "sentiment": "neutral",
                "follow_up_required": False,
            }
            job_enqueued = True

        return Result()


class FakeFailingSummaryService:
    async def create_summary(self, payload: dict):
        class Result:
            text = None
            data = None
            job_enqueued = False

        return Result()


def build_structured_summary_service() -> FakeStructuredSummaryService:
    return FakeStructuredSummaryService()


@pytest.mark.anyio
async def test_call_completion_persists_usage_and_enqueues_jobs(db_session, active_user) -> None:
    call = Call(
        id=uuid4(),
        user_id=active_user.id,
        caller_number="+33111111111",
        status="pending",
    )
    db_session.add(call)
    await db_session.commit()

    service = CallLifecycleService(
        db_session,
        summary_service=build_structured_summary_service(),
        recording_service=RecordingService(provider=FakeStorageProvider()),
        notification_service=NotificationService(db_session, provider=FakeNotificationProvider()),
    )

    result = await service.finalize_call(
        {
            "user_id": active_user.id,
            "call_id": str(call.id),
            "duration_seconds": 61,
            "minutes_remaining": 10,
            "caller_number": "+33111111111",
            "transcript": [
                {"speaker": "CALLER", "text": "I want to know your opening hours."},
                {"speaker": "AGENT", "text": "We are open from nine to five."},
            ],
            "recording_bytes": b"fake-audio",
        }
    )

    assert result.minutes_charged == 2
    assert result.summary_job_enqueued is True
    assert result.recording_job_enqueued is True
    assert result.notification_job_enqueued is True
    assert "opening hours" in result.summary_text
    assert result.recording_key.endswith(".mp3")

    refreshed_call = await db_session.get(Call, call.id)
    assert refreshed_call.summary_text == result.summary_text
    assert refreshed_call.summary_data["caller_intent"] == "Ask about opening hours"
    assert refreshed_call.recording_url == f"s3://recordings/{result.recording_key}"

    call_messages = (
        await db_session.execute(
            select(CallMessage)
            .where(CallMessage.call_id == call.id)
            .order_by(CallMessage.sequence_number.asc())
        )
    ).scalars().all()
    assert [message.text for message in call_messages] == [
        "I want to know your opening hours.",
        "We are open from nine to five.",
    ]

    notifications = (
        await db_session.execute(select(Notification).where(Notification.call_id == call.id))
    ).scalars().all()
    assert len(notifications) == 1
    assert notifications[0].notification_type == "call_completed"


@pytest.mark.anyio
async def test_call_completion_persists_structured_summary_data(
    db_session, active_user
) -> None:
    call = Call(
        id=uuid4(),
        user_id=active_user.id,
        caller_number="+33111111111",
        status="pending",
    )
    db_session.add(call)
    await db_session.commit()

    service = CallLifecycleService(
        db_session,
        summary_service=FakeStructuredSummaryService(),
        recording_service=RecordingService(provider=FakeStorageProvider()),
        notification_service=NotificationService(db_session, provider=FakeNotificationProvider()),
    )

    result = await service.finalize_call(
        {
            "user_id": active_user.id,
            "call_id": str(call.id),
            "duration_seconds": 61,
            "minutes_remaining": 10,
            "caller_number": "+33111111111",
            "transcript": [{"speaker": "CALLER", "text": "What are your opening hours?"}],
        }
    )

    refreshed_call = await db_session.get(Call, call.id)

    assert result.summary_text == "Caller asked about opening hours."
    assert refreshed_call.summary_text == "Caller asked about opening hours."
    assert refreshed_call.summary_data["caller_intent"] == "Ask about opening hours"
    assert refreshed_call.summary_data["follow_up_required"] is False


@pytest.mark.anyio
async def test_call_completion_records_failed_notification_but_still_completes(
    db_session, active_user
) -> None:
    call = Call(
        id=uuid4(),
        user_id=active_user.id,
        caller_number="+33111111111",
        status="pending",
    )
    db_session.add(call)
    await db_session.commit()

    service = CallLifecycleService(
        db_session,
        summary_service=build_structured_summary_service(),
        recording_service=RecordingService(provider=FakeStorageProvider()),
        notification_service=NotificationService(db_session, provider=FakeFailingNotificationProvider()),
    )

    result = await service.finalize_call(
        {
            "user_id": active_user.id,
            "call_id": str(call.id),
            "duration_seconds": 61,
            "minutes_remaining": 10,
            "caller_number": "+33111111111",
            "transcript": [{"speaker": "CALLER", "text": "Call me back."}],
        }
    )

    refreshed_call = await db_session.get(Call, call.id)
    assert refreshed_call.status == "completed"
    assert result.notification_job_enqueued is False

    notifications = (
        await db_session.execute(select(Notification).where(Notification.call_id == call.id))
    ).scalars().all()
    assert len(notifications) == 1
    assert notifications[0].notification_type == "call_completed"
    assert notifications[0].status == "failed"
    assert notifications[0].payload["notification_error"] == "messaging misconfigured"


@pytest.mark.anyio
async def test_call_completion_continues_when_summary_generation_fails(
    db_session, active_user
) -> None:
    call = Call(
        id=uuid4(),
        user_id=active_user.id,
        caller_number="+33111111111",
        status="pending",
    )
    db_session.add(call)
    await db_session.commit()

    service = CallLifecycleService(
        db_session,
        summary_service=FakeFailingSummaryService(),
        recording_service=RecordingService(provider=FakeStorageProvider()),
        notification_service=NotificationService(db_session, provider=FakeNotificationProvider()),
    )

    result = await service.finalize_call(
        {
            "user_id": active_user.id,
            "call_id": str(call.id),
            "duration_seconds": 61,
            "minutes_remaining": 10,
            "caller_number": "+33111111111",
            "transcript": [{"speaker": "CALLER", "text": "What are your opening hours?"}],
        }
    )

    refreshed_call = await db_session.get(Call, call.id)

    assert result.summary_text is None
    assert result.summary_job_enqueued is False
    assert refreshed_call.summary_text is None
    assert refreshed_call.summary_data is None
    assert refreshed_call.status == "completed"


@pytest.mark.anyio
async def test_call_finalization_job_skips_duplicate_completed_call(
    db_session, active_user, monkeypatch: pytest.MonkeyPatch
) -> None:
    call = Call(
        id=uuid4(),
        user_id=active_user.id,
        caller_number="+33111111111",
        status="pending",
    )
    db_session.add(call)
    await db_session.commit()

    from app.workers.jobs import call_finalization as call_finalization_module

    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    monkeypatch.setattr(call_finalization_module, "get_session_factory", lambda: session_factory)
    monkeypatch.setattr(
        call_finalization_module,
        "CallLifecycleService",
        lambda session: CallLifecycleService(
            session,
            summary_service=build_structured_summary_service(),
        ),
    )

    payload = {
        "user_id": str(active_user.id),
        "call_id": str(call.id),
        "duration_seconds": 61,
        "minutes_remaining": 10,
        "caller_number": "+33111111111",
        "transcript": [{"speaker": "CALLER", "text": "Call me back."}],
    }

    first_result = await call_finalization_module.call_finalization_job({}, payload)
    second_result = await call_finalization_module.call_finalization_job({}, payload)

    assert first_result["status"] == "completed"
    assert second_result["status"] == "skipped"

    messages = (
        await db_session.execute(select(CallMessage).where(CallMessage.call_id == call.id))
    ).scalars().all()
    assert len(messages) == 1

    notifications = (
        await db_session.execute(select(Notification).where(Notification.call_id == call.id))
    ).scalars().all()
    assert len(notifications) == 1


@pytest.mark.anyio
async def test_minute_exhaustion_disables_number(db_session, active_user) -> None:
    call = Call(
        id=uuid4(),
        user_id=active_user.id,
        caller_number="+33222222222",
        status="pending",
    )
    phone_number = PhoneNumber(
        user_id=active_user.id,
        e164="+33999888777",
        country_code="FR",
        provider="telnyx",
        provider_number_id="pn_456",
        provider_connection_name="app-active",
        is_active=True,
    )
    db_session.add(call)
    db_session.add(phone_number)
    await db_session.commit()

    telephony_service = TelephonyService(db_session, provider=FakeTelephonyProvider())
    service = CallLifecycleService(
        db_session,
        telephony_service=telephony_service,
        summary_service=build_structured_summary_service(),
        recording_service=RecordingService(provider=FakeStorageProvider()),
        notification_service=NotificationService(db_session, provider=FakeNotificationProvider()),
    )

    result = await service.finalize_call(
        {
            "user_id": active_user.id,
            "call_id": str(call.id),
            "duration_seconds": 61,
            "minutes_remaining": 1,
            "caller_number": "+33222222222",
            "transcript": [{"speaker": "CALLER", "text": "Call me back."}],
        }
    )

    assert result.number_disabled is True
    refreshed_number = await db_session.get(PhoneNumber, phone_number.id)
    assert refreshed_number.provider_connection_name == "app-disabled"
