import logging
import pytest
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.call import Call
from app.models.call_message import CallMessage
from app.models.notification import Notification
from app.models.phone_number import PhoneNumber
from app.models.usage_ledger import UsageLedger
from app.repositories.call_repository import CallRepository
from app.repositories.message_repository import MessageRepository
from app.repositories.notification_repository import NotificationRepository
from app.repositories.phone_number_repository import PhoneNumberRepository
from app.repositories.user_repository import UserRepository
from app.services.call_lifecycle_service import CallLifecycleService
from app.services.notification_service import NotificationService
from app.services.recording_service import RecordingService
from app.services.telephony_service import TelephonyService
from app.services.usage_accounting_service import UsageAccountingService


class FakeTelephonyProvider:
    async def disable_number(self, *, provider_number_id: str) -> str:
        return "app-disabled"


class FakeFailingDisableTelephonyProvider:
    async def disable_number(self, *, provider_number_id: str) -> str:
        raise RuntimeError("TELNYX_AUTHORIZATION_SENTINEL_FROM_DISABLE")


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


class CapturingSummaryService(FakeStructuredSummaryService):
    def __init__(self) -> None:
        self.transcripts: list[list[dict]] = []

    async def create_summary(self, payload: dict):
        self.transcripts.append(payload.get("transcript") or [])
        return await super().create_summary(payload)


def build_structured_summary_service() -> FakeStructuredSummaryService:
    return FakeStructuredSummaryService()


@pytest.mark.anyio
async def test_call_completion_reconstructs_full_ordered_transcript_for_summary(
    db_session,
    active_user,
) -> None:
    call = Call(id=uuid4(), user_id=active_user.id, status="connected")
    db_session.add_all(
        [
            call,
            UsageLedger(
                user_id=active_user.id,
                event_type="subscription_activated",
                source_id="in_full_transcript_summary",
                minutes_delta=10,
                balance_after=10,
            ),
        ]
    )
    await db_session.flush()
    db_session.add_all(
        [
            CallMessage(
                call_id=call.id,
                sequence_number=1,
                speaker="CALLER",
                text="First durable line",
            ),
            CallMessage(
                call_id=call.id,
                sequence_number=2,
                speaker="AGENT",
                text="Second durable line",
            ),
        ]
    )
    await db_session.commit()
    summary = CapturingSummaryService()

    await build_lifecycle_service(
        db_session,
        summary_service=summary,
    ).finalize_call(
        {
            "call_id": str(call.id),
            "duration_seconds": 10,
            "transcript": [
                {
                    "sequence_number": 3,
                    "speaker": "CALLER",
                    "text": "Recovery tail",
                }
            ],
        }
    )

    assert summary.transcripts == [
        [
            {
                "sequence_number": 1,
                "speaker": "CALLER",
                "text": "First durable line",
            },
            {
                "sequence_number": 2,
                "speaker": "AGENT",
                "text": "Second durable line",
            },
            {
                "sequence_number": 3,
                "speaker": "CALLER",
                "text": "Recovery tail",
            },
        ]
    ]


@pytest.mark.anyio
async def test_already_debited_retry_still_merges_late_recovery(
    db_session,
    active_user,
) -> None:
    call = Call(id=uuid4(), user_id=active_user.id, status="connected")
    db_session.add_all(
        [
            call,
            UsageLedger(
                user_id=active_user.id,
                event_type="subscription_activated",
                source_id="in_late_recovery",
                minutes_delta=10,
                balance_after=10,
            ),
        ]
    )
    await db_session.commit()
    service = build_lifecycle_service(db_session)
    await service.finalize_call(
        {
            "call_id": str(call.id),
            "duration_seconds": 1,
            "transcript": [
                {"sequence_number": 1, "speaker": "CALLER", "text": "Initial"}
            ],
        }
    )

    retry = await service.finalize_call(
        {
            "call_id": str(call.id),
            "duration_seconds": 1,
            "transcript": [
                {"sequence_number": 2, "speaker": "AGENT", "text": "Late recovery"}
            ],
        }
    )

    assert retry.already_completed is True
    rows = list(
        (
            await db_session.execute(
                select(CallMessage)
                .where(CallMessage.call_id == call.id)
                .order_by(CallMessage.sequence_number)
            )
        ).scalars()
    )
    assert [(row.sequence_number, row.text) for row in rows] == [
        (1, "Initial"),
        (2, "Late recovery"),
    ]


def build_lifecycle_service(
    session,
    *,
    summary_service=None,
    recording_service=None,
    notification_service=None,
    telephony_service=None,
) -> CallLifecycleService:
    return CallLifecycleService(
        session,
        call_repository=CallRepository(session),
        message_repository=MessageRepository(session),
        usage_accounting_service=UsageAccountingService(session),
        phone_number_repository=PhoneNumberRepository(session),
        telephony_service=telephony_service or TelephonyService(session, provider=FakeTelephonyProvider()),
        summary_service=summary_service or build_structured_summary_service(),
        recording_service=recording_service or RecordingService(provider=FakeStorageProvider()),
        notification_service=notification_service or NotificationService(
            provider=FakeNotificationProvider(),
            notification_repository=NotificationRepository(session),
        ),
    )


@pytest.mark.anyio
async def test_call_completion_persists_usage_and_enqueues_jobs(db_session, active_user) -> None:
    call = Call(
        id=uuid4(),
        user_id=active_user.id,
        caller_number="+33111111111",
        status="pending",
    )
    db_session.add_all(
        [
            call,
            UsageLedger(
                user_id=active_user.id,
                event_type="subscription_activated",
                source_id="in_post_call_jobs",
                minutes_delta=10,
                balance_after=10,
            ),
        ]
    )
    await db_session.commit()

    service = build_lifecycle_service(db_session)

    result = await service.finalize_call(
        {
            "call_id": str(call.id),
            "duration_seconds": 61,
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
async def test_call_completion_uses_persisted_owner_and_balance(
    db_session,
    active_user,
) -> None:
    other_user = await UserRepository(db_session).create(
        clerk_user_id="user_other_accounting",
        email="other-accounting@example.com",
    )
    call = Call(
        id=uuid4(),
        user_id=active_user.id,
        caller_number="+33111111111",
        status="awaiting_accounting",
    )
    phone_number = PhoneNumber(
        user_id=active_user.id,
        e164="+33999888770",
        country_code="FR",
        provider="telnyx",
        provider_number_id="pn_authoritative_owner",
        provider_connection_name="app-active",
        is_active=True,
    )
    db_session.add_all(
        [
            call,
            phone_number,
            UsageLedger(
                user_id=active_user.id,
                event_type="subscription_activated",
                source_id="in_authoritative_owner",
                minutes_delta=1,
                balance_after=1,
            ),
        ]
    )
    await db_session.commit()

    result = await build_lifecycle_service(db_session).finalize_call(
        {
            "call_id": str(call.id),
            "user_id": other_user.id,
            "minutes_remaining": 999,
            "duration_seconds": 61,
            "caller_number": "+33111111111",
            "transcript": [{"speaker": "CALLER", "text": "Who owns this call?"}],
            "recording_bytes": b"fake-audio",
        }
    )

    debit = await db_session.scalar(
        select(UsageLedger).where(
            UsageLedger.call_id == call.id,
            UsageLedger.event_type == "call_completed",
        )
    )
    notification = await db_session.scalar(
        select(Notification).where(Notification.call_id == call.id)
    )

    assert result.minutes_charged == 1
    assert result.number_disabled is True
    assert debit is not None
    assert debit.user_id == active_user.id
    assert debit.minutes_delta == -1
    assert debit.balance_after == 0
    assert notification is not None
    assert notification.user_id == active_user.id
    assert result.recording_key == f"calls/{active_user.id}/{call.id}.mp3"


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

    service = build_lifecycle_service(db_session)

    result = await service.finalize_call(
        {
            "call_id": str(call.id),
            "duration_seconds": 61,
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

    service = build_lifecycle_service(
        db_session,
        notification_service=NotificationService(
            provider=FakeFailingNotificationProvider(),
            notification_repository=NotificationRepository(db_session),
        ),
    )

    result = await service.finalize_call(
        {
            "call_id": str(call.id),
            "duration_seconds": 61,
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

    service = build_lifecycle_service(
        db_session,
        summary_service=FakeFailingSummaryService(),
    )

    result = await service.finalize_call(
        {
            "call_id": str(call.id),
            "duration_seconds": 61,
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
        "_build_lifecycle_service",
        lambda session: build_lifecycle_service(session),
    )

    payload = {
        "call_id": str(call.id),
        "duration_seconds": 61,
        "caller_number": "+33111111111",
        "transcript": [{"speaker": "CALLER", "text": "Call me back."}],
    }

    class MockRedisLock:
        async def __aenter__(self): return self
        async def __aexit__(self, exc_type, exc_val, exc_tb): pass

    class MockRedis:
        def lock(self, *args, **kwargs): return MockRedisLock()

    ctx = {"redis": MockRedis()}

    first_result = await call_finalization_module.call_finalization_job(ctx, payload)
    second_result = await call_finalization_module.call_finalization_job(ctx, payload)

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
    db_session.add_all(
        [
            call,
            phone_number,
            UsageLedger(
                user_id=active_user.id,
                event_type="subscription_activated",
                source_id="in_minute_exhaustion",
                minutes_delta=1,
                balance_after=1,
            ),
        ]
    )
    await db_session.commit()

    service = build_lifecycle_service(db_session)

    result = await service.finalize_call(
        {
            "call_id": str(call.id),
            "duration_seconds": 61,
            "caller_number": "+33222222222",
            "transcript": [{"speaker": "CALLER", "text": "Call me back."}],
        }
    )

    assert result.minutes_charged == 1
    assert result.number_disabled is True
    refreshed_number = await db_session.get(PhoneNumber, phone_number.id)
    assert refreshed_number.provider_connection_name == "app-disabled"


@pytest.mark.anyio
async def test_disable_number_failure_does_not_render_provider_exception(
    db_session,
    active_user,
    caplog,
) -> None:
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
    db_session.add_all(
        [
            call,
            phone_number,
            UsageLedger(
                user_id=active_user.id,
                event_type="subscription_activated",
                source_id="in_disable_failure",
                minutes_delta=1,
                balance_after=1,
            ),
        ]
    )
    await db_session.commit()
    service = build_lifecycle_service(
        db_session,
        telephony_service=TelephonyService(
            db_session,
            provider=FakeFailingDisableTelephonyProvider(),
        ),
    )

    with caplog.at_level(logging.ERROR):
        result = await service.finalize_call(
            {
                "call_id": str(call.id),
                "duration_seconds": 61,
                "caller_number": "+33222222222",
                "transcript": [{"speaker": "CALLER", "text": "Call me back."}],
            }
        )

    assert result.number_disabled is False
    assert "TELNYX_AUTHORIZATION_SENTINEL_FROM_DISABLE" not in caplog.text
    assert "event=phone_number_disable_failed" in caplog.text
    assert "operation=disable_phone_number" in caplog.text
    assert f"call_id={call.id}" in caplog.text
    assert f"user_id={active_user.id}" in caplog.text
    assert all(record.exc_info is None for record in caplog.records)
