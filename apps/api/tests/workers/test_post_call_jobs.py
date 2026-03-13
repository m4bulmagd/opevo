import pytest
from uuid import uuid4

from sqlalchemy import select

from app.models.call import Call
from app.models.call_message import CallMessage
from app.models.notification import Notification
from app.models.phone_number import PhoneNumber
from app.services.call_lifecycle_service import CallLifecycleService
from app.services.telephony_service import TelephonyService


class FakeTelephonyProvider:
    async def disable_number(self, *, provider_number_id: str) -> str:
        return "app-disabled"


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

    service = CallLifecycleService(db_session)

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
    service = CallLifecycleService(db_session, telephony_service=telephony_service)

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
