"""
Unit tests for individual ARQ worker jobs:
  - summary_job     (app/workers/jobs/summary.py)
  - recording_job   (app/workers/jobs/recording.py)
  - notifications_job (app/workers/jobs/notifications.py)
  - transcript_flush_job (app/workers/jobs/transcript_flush.py)

Each job is tested in isolation using fake providers/services and, where the job
reaches out to the database via get_session_factory, by monkeypatching that
factory with the in-memory SQLite session from the shared db_session fixture.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from types import SimpleNamespace
import traceback
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.call import Call
from app.models.call_message import CallMessage
from app.models.notification import Notification
from app.models.phone_number import PhoneNumber
from app.services.notification_service import NotificationService
from app.repositories.notification_repository import NotificationRepository

# ---------------------------------------------------------------------------
# Shared fake context (ARQ passes a ctx dict to each job)
# ---------------------------------------------------------------------------

CTX: dict = {}


# ===========================================================================
# summary_job tests
# ===========================================================================


@dataclass(frozen=True)
class FakeSummaryResult:
    text: str | None
    data: dict | None
    job_enqueued: bool


class FakeSummaryService:
    """SummaryService replacement that returns a successful result."""

    async def create_summary(self, payload: dict) -> FakeSummaryResult:
        return FakeSummaryResult(
            text="Caller enquired about opening hours.",
            data={
                "summary_text": "Caller enquired about opening hours.",
                "caller_intent": "Ask about opening hours",
                "action_items": ["Provide opening hours"],
                "sentiment": "neutral",
                "follow_up_required": False,
            },
            job_enqueued=True,
        )


class FakeFailingSummaryService:
    """SummaryService replacement that signals a generation failure."""

    async def create_summary(self, payload: dict) -> FakeSummaryResult:
        return FakeSummaryResult(text=None, data=None, job_enqueued=False)


@pytest.mark.anyio
async def test_summary_job_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """summary_job returns structured data when the provider succeeds."""
    from app.workers.jobs import summary as summary_module

    monkeypatch.setattr(summary_module, "SummaryService", lambda provider=None: FakeSummaryService())

    payload = {
        "call_id": str(uuid4()),
        "user_id": str(uuid4()),
        "transcript": [
            {"speaker": "CALLER", "text": "What are your opening hours?"},
            {"speaker": "AGENT", "text": "We are open nine to five."},
        ],
    }

    result = await summary_module.summary_job(CTX, payload)

    assert result["summary_text"] == "Caller enquired about opening hours."
    assert result["summary_data"]["caller_intent"] == "Ask about opening hours"
    assert result["job_enqueued"] is True


@pytest.mark.anyio
async def test_summary_job_provider_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """summary_job returns empty result when the provider cannot generate a summary."""
    from app.workers.jobs import summary as summary_module

    monkeypatch.setattr(summary_module, "SummaryService", lambda provider=None: FakeFailingSummaryService())

    payload = {
        "call_id": str(uuid4()),
        "user_id": str(uuid4()),
        "transcript": [],
    }

    result = await summary_module.summary_job(CTX, payload)

    assert result["summary_text"] is None
    assert result["summary_data"] is None
    assert result["job_enqueued"] is False


@pytest.mark.anyio
async def test_summary_job_empty_transcript(monkeypatch: pytest.MonkeyPatch) -> None:
    """summary_job handles a payload with no transcript key gracefully."""
    from app.workers.jobs import summary as summary_module

    monkeypatch.setattr(summary_module, "SummaryService", lambda provider=None: FakeFailingSummaryService())

    # transcript key is entirely absent — payload is "missing" the field
    payload: dict = {"call_id": str(uuid4()), "user_id": str(uuid4())}

    result = await summary_module.summary_job(CTX, payload)

    assert result["job_enqueued"] is False


# ===========================================================================
# recording_job tests
# ===========================================================================


@dataclass(frozen=True)
class FakeRecordingResult:
    object_key: str | None
    url: str | None
    job_enqueued: bool


class FakeRecordingService:
    """RecordingService replacement that simulates a successful upload."""

    async def store_recording(self, payload: dict) -> FakeRecordingResult:
        call_id = payload.get("call_id", "unknown")
        user_id = payload.get("user_id", "unknown")
        key = f"calls/{user_id}/{call_id}.mp3"
        return FakeRecordingResult(
            object_key=key,
            url=f"s3://recordings/{key}",
            job_enqueued=True,
        )


class FakeFailingRecordingService:
    """RecordingService replacement that simulates a storage failure."""

    async def store_recording(self, payload: dict) -> FakeRecordingResult:
        return FakeRecordingResult(object_key=None, url=None, job_enqueued=False)


@pytest.mark.anyio
async def test_recording_job_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """recording_job returns object key and URL when storage succeeds."""
    from app.workers.jobs import recording as recording_module

    monkeypatch.setattr(recording_module, "RecordingService", lambda provider=None: FakeRecordingService())

    call_id = str(uuid4())
    user_id = str(uuid4())
    payload = {
        "call_id": call_id,
        "user_id": user_id,
        "recording_bytes": b"fake-audio-bytes",
    }

    result = await recording_module.recording_job(CTX, payload)

    assert result["recording_key"] == f"calls/{user_id}/{call_id}.mp3"
    assert result["recording_url"].endswith(f"{call_id}.mp3")
    assert result["job_enqueued"] is True


@pytest.mark.anyio
async def test_recording_job_storage_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """recording_job returns empty result when the storage provider fails."""
    from app.workers.jobs import recording as recording_module

    monkeypatch.setattr(recording_module, "RecordingService", lambda provider=None: FakeFailingRecordingService())

    payload = {
        "call_id": str(uuid4()),
        "user_id": str(uuid4()),
        "recording_bytes": b"bad-data",
    }

    result = await recording_module.recording_job(CTX, payload)

    assert result["recording_key"] is None
    assert result["recording_url"] is None
    assert result["job_enqueued"] is False


@pytest.mark.anyio
async def test_recording_job_missing_recording_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    """recording_job handles payload missing recording_bytes gracefully."""
    from app.workers.jobs import recording as recording_module

    monkeypatch.setattr(recording_module, "RecordingService", lambda provider=None: FakeFailingRecordingService())

    # recording_bytes key is absent
    payload: dict = {"call_id": str(uuid4()), "user_id": str(uuid4())}

    result = await recording_module.recording_job(CTX, payload)

    assert result["job_enqueued"] is False


# ===========================================================================
# notifications_job tests
# ===========================================================================


class FakeNotificationProvider:
    """Provider that reports a successful send."""

    async def send_notification(self, *, user_id, notification_type: str, payload: dict) -> str:
        return "sent"


class FakeFailingNotificationProvider:
    """Provider that raises to simulate a delivery failure."""

    async def send_notification(self, *, user_id, notification_type: str, payload: dict) -> str:
        raise RuntimeError("push service unavailable")


def _make_notification_service(session, provider) -> NotificationService:
    return NotificationService(
        provider=provider,
        notification_repository=NotificationRepository(session),
    )


@pytest.mark.anyio
async def test_notifications_job_happy_path(
    db_session, active_user, monkeypatch: pytest.MonkeyPatch
) -> None:
    """notifications_job persists a sent notification and returns job_enqueued=True."""
    call = Call(
        id=uuid4(),
        user_id=active_user.id,
        caller_number="+33111111111",
        status="completed",
    )
    db_session.add(call)
    await db_session.commit()

    from app.workers.jobs import notifications as notifications_module

    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    monkeypatch.setattr(notifications_module, "get_session_factory", lambda: session_factory)
    monkeypatch.setattr(
        notifications_module,
        "NotificationService",
        lambda provider, notification_repository: NotificationService(
            provider=FakeNotificationProvider(),
            notification_repository=notification_repository,
        ),
    )

    payload = {
        "user_id": active_user.id,  # UUID object — job passes this directly to the service
        "call_id": str(call.id),
        "summary_text": "Caller asked about hours.",
        "minutes_charged": 2,
    }

    result = await notifications_module.notifications_job(CTX, payload)

    assert result["status"] == "sent"
    assert result["job_enqueued"] is True

    notifications = (
        await db_session.execute(
            select(Notification).where(Notification.call_id == call.id)
        )
    ).scalars().all()
    assert len(notifications) == 1
    assert notifications[0].notification_type == "call_completed"
    assert notifications[0].status == "sent"


@pytest.mark.anyio
async def test_notifications_job_provider_failure(
    db_session, active_user, monkeypatch: pytest.MonkeyPatch
) -> None:
    """notifications_job persists a failed notification when the provider raises."""
    call = Call(
        id=uuid4(),
        user_id=active_user.id,
        caller_number="+33111111111",
        status="completed",
    )
    db_session.add(call)
    await db_session.commit()

    from app.workers.jobs import notifications as notifications_module

    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    monkeypatch.setattr(notifications_module, "get_session_factory", lambda: session_factory)
    monkeypatch.setattr(
        notifications_module,
        "NotificationService",
        lambda provider, notification_repository: NotificationService(
            provider=FakeFailingNotificationProvider(),
            notification_repository=notification_repository,
        ),
    )

    payload = {
        "user_id": active_user.id,  # UUID object — job passes this directly to the service
        "call_id": str(call.id),
        "summary_text": None,
        "minutes_charged": 1,
    }

    result = await notifications_module.notifications_job(CTX, payload)

    assert result["status"] == "failed"
    assert result["job_enqueued"] is False

    notifications = (
        await db_session.execute(
            select(Notification).where(Notification.call_id == call.id)
        )
    ).scalars().all()
    assert len(notifications) == 1
    assert notifications[0].status == "failed"
    assert "push service unavailable" in notifications[0].payload.get("notification_error", "")


@pytest.mark.anyio
async def test_notifications_job_missing_user_id(
    db_session, active_user, monkeypatch: pytest.MonkeyPatch
) -> None:
    """notifications_job raises KeyError when user_id is absent from payload."""
    from app.workers.jobs import notifications as notifications_module

    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    monkeypatch.setattr(notifications_module, "get_session_factory", lambda: session_factory)
    monkeypatch.setattr(
        notifications_module,
        "NotificationService",
        lambda provider, notification_repository: NotificationService(
            provider=FakeNotificationProvider(),
            notification_repository=notification_repository,
        ),
    )

    # call_id present but user_id is missing
    payload: dict = {
        "call_id": str(uuid4()),
        "minutes_charged": 1,
    }

    with pytest.raises(KeyError):
        await notifications_module.notifications_job(CTX, payload)


# ===========================================================================
# transcript_flush_job tests
# ===========================================================================


@pytest.mark.anyio
async def test_transcript_flush_job_happy_path(
    db_session, active_user, monkeypatch: pytest.MonkeyPatch
) -> None:
    """transcript_flush_job persists all transcript lines and returns the payload."""
    call = Call(
        id=uuid4(),
        user_id=active_user.id,
        caller_number="+33111111111",
        status="completed",
    )
    db_session.add(call)
    await db_session.commit()

    from app.workers.jobs import transcript_flush as transcript_flush_module

    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    monkeypatch.setattr(transcript_flush_module, "get_session_factory", lambda: session_factory)

    transcript = [
        {"speaker": "CALLER", "text": "Hello, what are your hours?"},
        {"speaker": "AGENT", "text": "We are open nine to five."},
    ]
    payload = {
        "call_id": str(call.id),
        "transcript": transcript,
    }

    returned = await transcript_flush_module.transcript_flush_job(CTX, payload)

    # job echoes the payload back
    assert returned["call_id"] == str(call.id)

    messages = (
        await db_session.execute(
            select(CallMessage)
            .where(CallMessage.call_id == call.id)
            .order_by(CallMessage.sequence_number.asc())
        )
    ).scalars().all()
    assert len(messages) == 2
    assert messages[0].speaker == "CALLER"
    assert messages[0].text == "Hello, what are your hours?"
    assert messages[1].speaker == "AGENT"
    assert messages[1].text == "We are open nine to five."


@pytest.mark.anyio
async def test_transcript_flush_job_empty_transcript(
    db_session, active_user, monkeypatch: pytest.MonkeyPatch
) -> None:
    """transcript_flush_job handles an empty transcript without error."""
    call = Call(
        id=uuid4(),
        user_id=active_user.id,
        caller_number="+33111111111",
        status="completed",
    )
    db_session.add(call)
    await db_session.commit()

    from app.workers.jobs import transcript_flush as transcript_flush_module

    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    monkeypatch.setattr(transcript_flush_module, "get_session_factory", lambda: session_factory)

    payload = {"call_id": str(call.id), "transcript": []}

    returned = await transcript_flush_module.transcript_flush_job(CTX, payload)

    assert returned["call_id"] == str(call.id)

    messages = (
        await db_session.execute(select(CallMessage).where(CallMessage.call_id == call.id))
    ).scalars().all()
    assert len(messages) == 0


@pytest.mark.anyio
async def test_transcript_flush_job_missing_transcript_key(
    db_session, active_user, monkeypatch: pytest.MonkeyPatch
) -> None:
    """transcript_flush_job treats a missing transcript key as an empty list."""
    call = Call(
        id=uuid4(),
        user_id=active_user.id,
        caller_number="+33111111111",
        status="completed",
    )
    db_session.add(call)
    await db_session.commit()

    from app.workers.jobs import transcript_flush as transcript_flush_module

    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    monkeypatch.setattr(transcript_flush_module, "get_session_factory", lambda: session_factory)

    # transcript key is entirely absent
    payload: dict = {"call_id": str(call.id)}

    returned = await transcript_flush_module.transcript_flush_job(CTX, payload)

    # job still returns the payload
    assert "call_id" in returned

    messages = (
        await db_session.execute(select(CallMessage).where(CallMessage.call_id == call.id))
    ).scalars().all()
    assert len(messages) == 0


@pytest.mark.anyio
async def test_transcript_flush_job_missing_call_id(
    db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """transcript_flush_job raises when call_id is absent from the payload."""
    from app.workers.jobs import transcript_flush as transcript_flush_module

    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    monkeypatch.setattr(transcript_flush_module, "get_session_factory", lambda: session_factory)

    payload: dict = {"transcript": [{"speaker": "CALLER", "text": "Hi"}]}

    with pytest.raises((KeyError, TypeError)):
        await transcript_flush_module.transcript_flush_job(CTX, payload)


# ===========================================================================
# phone_provisioning_job tests
# ===========================================================================


class CapturingProvisioningProvider:
    def __init__(self) -> None:
        self.country_codes: list[str] = []
        self.operation_keys: list[str | None] = []

    async def provision_number(
        self,
        *,
        country_code: str,
        operation_key: str | None = None,
    ) -> dict:
        self.country_codes.append(country_code)
        self.operation_keys.append(operation_key)
        return {
            "e164": "+33123456789",
            "provider_number_id": "pn_123",
            "provider_connection_name": "app-active",
        }

    async def enable_number(self, *, provider_number_id: str) -> str:
        return "app-active"

    async def disable_number(self, *, provider_number_id: str) -> str:
        return "app-disabled"


class ReviewRequiredProvisioningProvider:
    async def provision_number(self, *, country_code: str) -> dict:
        from app.providers.telephony.base import TelephonyProvisioningReviewRequired

        raise TelephonyProvisioningReviewRequired(
            reason="no_affordable_number",
            payload={
                "event": "phone_number_provisioning_review_required",
                "country_code": country_code,
                "contact_support": True,
            },
        )

    async def enable_number(self, *, provider_number_id: str) -> str:
        return "app-active"

    async def disable_number(self, *, provider_number_id: str) -> str:
        return "app-disabled"


class FakePhoneProvisioningSession:
    def __init__(self) -> None:
        self.commits = 0

    async def commit(self) -> None:
        self.commits += 1


class FakePhoneProvisioningSessionContext:
    def __init__(self, session: FakePhoneProvisioningSession) -> None:
        self.session = session

    async def __aenter__(self) -> FakePhoneProvisioningSession:
        return self.session

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None


class CapturingPhoneProvisioningRepository:
    def __init__(self) -> None:
        self.failed_calls: list[dict] = []

    async def mark_running(self, **kwargs):
        return SimpleNamespace(
            provider_operation_key=kwargs.get("operation_key")
        )

    async def mark_failed(self, **kwargs) -> None:
        self.failed_calls.append(kwargs)


class CapturingPhoneProvisioningNotificationRepository:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def create(self, **kwargs) -> None:
        self.calls.append(kwargs)


def install_phone_provisioning_job_fakes(
    monkeypatch: pytest.MonkeyPatch,
    *,
    error: Exception,
) -> tuple[
    FakePhoneProvisioningSession,
    CapturingPhoneProvisioningRepository,
    CapturingPhoneProvisioningNotificationRepository,
]:
    from app.workers.jobs import phone_provisioning as phone_provisioning_module

    session = FakePhoneProvisioningSession()
    provisioning_repository = CapturingPhoneProvisioningRepository()
    notification_repository = CapturingPhoneProvisioningNotificationRepository()

    class FakeUserRepository:
        def __init__(self, _session) -> None:
            pass

        async def get_by_id(self, user_id: UUID):
            return SimpleNamespace(id=user_id, country_code="FR")

    class FailingTelephonyService:
        def __init__(self, _session, *, provider=None) -> None:
            pass

        async def provision_number(self, user_id: UUID, *, country_code: str):
            raise error

    monkeypatch.setattr(phone_provisioning_module, "UserRepository", FakeUserRepository)
    monkeypatch.setattr(
        phone_provisioning_module,
        "PhoneNumberProvisioningRepository",
        lambda _session: provisioning_repository,
    )
    monkeypatch.setattr(phone_provisioning_module, "TelephonyService", FailingTelephonyService)
    monkeypatch.setattr(
        phone_provisioning_module,
        "NotificationRepository",
        lambda _session: notification_repository,
    )

    return session, provisioning_repository, notification_repository


@pytest.mark.anyio
async def test_phone_provisioning_job_persists_successful_state_and_forces_fr_default(
    db_session, active_user
) -> None:
    from app.models.phone_number_provisioning import PhoneNumberProvisioning
    from app.workers.jobs.phone_provisioning import phone_provisioning_job

    active_user.country_code = None
    await db_session.commit()

    provider = CapturingProvisioningProvider()
    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    await phone_provisioning_job(
        {
            "telephony_provider": provider,
            "session_factory": session_factory,
        },
        {"user_id": str(active_user.id)},
        operation_key="outbox:phone-provision:evt_123",
    )

    provisionings = (
        await db_session.execute(
            select(PhoneNumberProvisioning).where(PhoneNumberProvisioning.user_id == active_user.id)
        )
    ).scalars().all()
    phone_numbers = (
        await db_session.execute(select(PhoneNumber).where(PhoneNumber.user_id == active_user.id))
    ).scalars().all()

    assert provider.country_codes == ["FR"]
    assert provider.operation_keys == ["outbox:phone-provision:evt_123"]
    assert len(phone_numbers) == 1
    assert len(provisionings) == 1
    assert provisionings[0].status == "succeeded"
    assert provisionings[0].attempt_count == 1
    assert provisionings[0].can_retry is False
    assert provisionings[0].phone_number_id == phone_numbers[0].id
    assert (
        provisionings[0].provider_operation_key
        == "outbox:phone-provision:evt_123"
    )


@pytest.mark.anyio
async def test_phone_provisioning_reuses_first_provider_key_across_customer_retry(
    db_session, active_user
) -> None:
    from app.models.phone_number_provisioning import PhoneNumberProvisioning
    from app.providers.telephony.base import TelephonyProvisioningReviewRequired
    from app.workers.jobs.phone_provisioning import phone_provisioning_job

    class RetryThenSucceedProvider(CapturingProvisioningProvider):
        async def provision_number(
            self,
            *,
            country_code: str,
            operation_key: str | None = None,
        ) -> dict:
            self.country_codes.append(country_code)
            self.operation_keys.append(operation_key)
            if len(self.operation_keys) == 1:
                raise TelephonyProvisioningReviewRequired(
                    reason="no_affordable_number",
                    payload={
                        "event": "phone_number_provisioning_review_required",
                        "country_code": country_code,
                        "contact_support": True,
                    },
                )
            return {
                "e164": "+33123456789",
                "provider_number_id": "pn_123",
                "provider_connection_name": "app-disabled",
            }

    provider = RetryThenSucceedProvider()
    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    await phone_provisioning_job(
        {
            "telephony_provider": provider,
            "session_factory": session_factory,
        },
        {"user_id": str(active_user.id)},
        operation_key="outbox:phone-provision:first-event",
    )
    await phone_provisioning_job(
        {
            "telephony_provider": provider,
            "session_factory": session_factory,
        },
        {"user_id": str(active_user.id)},
        operation_key="outbox:phone-provision:customer-retry-event",
    )

    provisioning = await db_session.scalar(
        select(PhoneNumberProvisioning).where(
            PhoneNumberProvisioning.user_id == active_user.id
        )
    )
    assert provisioning is not None
    assert provider.operation_keys == [
        "outbox:phone-provision:first-event",
        "outbox:phone-provision:first-event",
    ]
    assert (
        provisioning.provider_operation_key
        == "outbox:phone-provision:first-event"
    )
    assert provisioning.status == "succeeded"


@pytest.mark.anyio
async def test_phone_provisioning_pending_order_keeps_customer_retry_disabled(
    db_session, active_user
) -> None:
    from app.models.phone_number_provisioning import PhoneNumberProvisioning
    from app.providers.telephony.base import TelephonyProvisioningPending
    from app.workers.jobs.phone_provisioning import phone_provisioning_job

    class PendingProvider(CapturingProvisioningProvider):
        async def provision_number(
            self,
            *,
            country_code: str,
            operation_key: str | None = None,
        ) -> dict:
            self.operation_keys.append(operation_key)
            raise TelephonyProvisioningPending(reason="existing_order_pending")

    provider = PendingProvider()
    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    with pytest.raises(TelephonyProvisioningPending):
        await phone_provisioning_job(
            {
                "telephony_provider": provider,
                "session_factory": session_factory,
            },
            {"user_id": str(active_user.id)},
            operation_key="outbox:phone-provision:pending",
        )

    provisioning = await db_session.scalar(
        select(PhoneNumberProvisioning).where(
            PhoneNumberProvisioning.user_id == active_user.id
        )
    )
    assert provisioning is not None
    assert provisioning.status == "failed"
    assert provisioning.can_retry is False
    assert provisioning.last_error_reason == "existing_order_pending"
    assert (
        provisioning.provider_operation_key
        == "outbox:phone-provision:pending"
    )


@pytest.mark.anyio
async def test_phone_provisioning_job_persists_retryable_failure_state(
    db_session, active_user
) -> None:
    from app.models.phone_number_provisioning import PhoneNumberProvisioning
    from app.workers.jobs.phone_provisioning import phone_provisioning_job

    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    await phone_provisioning_job(
        {
            "telephony_provider": ReviewRequiredProvisioningProvider(),
            "session_factory": session_factory,
        },
        {"user_id": str(active_user.id)},
    )

    provisionings = (
        await db_session.execute(
            select(PhoneNumberProvisioning).where(PhoneNumberProvisioning.user_id == active_user.id)
        )
    ).scalars().all()
    notifications = (
        await db_session.execute(
            select(Notification).where(Notification.user_id == active_user.id)
        )
    ).scalars().all()
    phone_numbers = (
        await db_session.execute(select(PhoneNumber).where(PhoneNumber.user_id == active_user.id))
    ).scalars().all()

    assert len(provisionings) == 1
    assert provisionings[0].status == "failed"
    assert provisionings[0].attempt_count == 1
    assert provisionings[0].can_retry is True
    assert provisionings[0].last_error_reason == "no_affordable_number"
    assert not phone_numbers
    assert notifications[0].notification_type == "phone_number_provisioning_review_required"


@pytest.mark.anyio
async def test_phone_provisioning_review_failure_does_not_log_exception_message(
    monkeypatch: pytest.MonkeyPatch,
    caplog,
) -> None:
    from app.providers.telephony.base import TelephonyProvisioningReviewRequired
    from app.workers.jobs.phone_provisioning import phone_provisioning_job

    error = TelephonyProvisioningReviewRequired(
        reason="provider_review_required",
        payload={"event": "phone_number_provisioning_review_required"},
    )
    error.args = ("AUTHORIZATION_SENTINEL_FROM_REVIEW_EXCEPTION",)
    session, provisioning_repository, notification_repository = (
        install_phone_provisioning_job_fakes(monkeypatch, error=error)
    )

    with caplog.at_level(logging.WARNING):
        await phone_provisioning_job(
            {"session_factory": lambda: FakePhoneProvisioningSessionContext(session)},
            {"user_id": "00000000-0000-0000-0000-000000000123"},
        )

    assert "AUTHORIZATION_SENTINEL_FROM_REVIEW_EXCEPTION" not in caplog.text
    assert "event=phone_provisioning_review_required" in caplog.text
    assert "operation=provision_phone_number" in caplog.text
    assert "error_type=TelephonyProvisioningReviewRequired" in caplog.text
    assert provisioning_repository.failed_calls[0]["reason"] == "provider_review_required"
    assert notification_repository.calls
    assert all(record.exc_info is None for record in caplog.records)


@pytest.mark.anyio
async def test_phone_provisioning_unexpected_failure_does_not_log_or_persist_exception_message(
    monkeypatch: pytest.MonkeyPatch,
    caplog,
) -> None:
    from app.workers.jobs.phone_provisioning import phone_provisioning_job

    error_message = (
        "PHONE_SENTINEL_+33612345678 "
        "AUTHORIZATION_SENTINEL_FROM_PROVISIONING_PROVIDER"
    )
    session, provisioning_repository, _notification_repository = (
        install_phone_provisioning_job_fakes(
            monkeypatch,
            error=RuntimeError(error_message),
        )
    )

    with caplog.at_level(logging.ERROR):
        with pytest.raises(RuntimeError) as exc_info:
            await phone_provisioning_job(
                {"session_factory": lambda: FakePhoneProvisioningSessionContext(session)},
                {"user_id": "00000000-0000-0000-0000-000000000123"},
            )

    assert error_message not in str(exc_info.value)
    assert error_message not in caplog.text
    assert "+33612345678" not in caplog.text
    assert "event=phone_provisioning_failed" in caplog.text
    assert "operation=provision_phone_number" in caplog.text
    assert "error_type=RuntimeError" in caplog.text
    assert provisioning_repository.failed_calls[0]["reason"] == "RuntimeError"
    assert provisioning_repository.failed_calls[0]["payload"] == {
        "error_type": "RuntimeError",
    }
    assert all(record.exc_info is None for record in caplog.records)


@pytest.mark.anyio
async def test_phone_provisioning_sanitizes_sensitive_exception_class_name(
    monkeypatch: pytest.MonkeyPatch,
    caplog,
) -> None:
    from app.workers.jobs.phone_provisioning import phone_provisioning_job

    sensitive_type_sentinel = "ProviderAuthorizationTokenSentinelError"
    sensitive_error_type = type(sensitive_type_sentinel, (RuntimeError,), {})
    session, provisioning_repository, _notification_repository = (
        install_phone_provisioning_job_fakes(
            monkeypatch,
            error=sensitive_error_type("provider failure"),
        )
    )

    with caplog.at_level(logging.ERROR):
        with pytest.raises(RuntimeError) as exc_info:
            await phone_provisioning_job(
                {"session_factory": lambda: FakePhoneProvisioningSessionContext(session)},
                {"user_id": "00000000-0000-0000-0000-000000000123"},
            )

    assert sensitive_type_sentinel not in str(exc_info.value)
    assert sensitive_type_sentinel not in caplog.text
    assert provisioning_repository.failed_calls[0]["reason"] == "Exception"
    assert provisioning_repository.failed_calls[0]["payload"] == {
        "error_type": "Exception",
    }


def assert_exception_state_is_sanitized(
    error: BaseException,
    *sentinels: str,
) -> None:
    rendered_traceback = "".join(
        traceback.format_exception(type(error), error, error.__traceback__)
    )
    for sentinel in sentinels:
        assert sentinel not in str(error)
        assert sentinel not in rendered_traceback
    assert error.__context__ is None
    assert error.__cause__ is None


@pytest.mark.anyio
async def test_phone_provisioning_mark_failed_error_does_not_chain_provider_secrets(
    monkeypatch: pytest.MonkeyPatch,
    caplog,
) -> None:
    from app.workers.jobs.phone_provisioning import phone_provisioning_job

    provider_sentinel = "PROVIDER_AUTHORIZATION_SENTINEL_FROM_MARK_FAILED_PATH"
    persistence_sentinel = "PERSISTENCE_TOKEN_SENTINEL_FROM_MARK_FAILED"
    session, provisioning_repository, _notification_repository = (
        install_phone_provisioning_job_fakes(
            monkeypatch,
            error=RuntimeError(provider_sentinel),
        )
    )

    async def fail_mark_failed(**_kwargs) -> None:
        raise RuntimeError(persistence_sentinel)

    monkeypatch.setattr(provisioning_repository, "mark_failed", fail_mark_failed)

    with caplog.at_level(logging.ERROR):
        with pytest.raises(RuntimeError) as exc_info:
            await phone_provisioning_job(
                {"session_factory": lambda: FakePhoneProvisioningSessionContext(session)},
                {"user_id": "00000000-0000-0000-0000-000000000123"},
            )

    assert_exception_state_is_sanitized(
        exc_info.value,
        provider_sentinel,
        persistence_sentinel,
    )
    assert provider_sentinel not in caplog.text
    assert persistence_sentinel not in caplog.text


@pytest.mark.anyio
async def test_phone_provisioning_commit_error_does_not_chain_provider_secrets(
    monkeypatch: pytest.MonkeyPatch,
    caplog,
) -> None:
    from app.workers.jobs.phone_provisioning import phone_provisioning_job

    provider_sentinel = "PROVIDER_AUTHORIZATION_SENTINEL_FROM_COMMIT_PATH"
    persistence_sentinel = "PERSISTENCE_TOKEN_SENTINEL_FROM_COMMIT"
    session, _provisioning_repository, _notification_repository = (
        install_phone_provisioning_job_fakes(
            monkeypatch,
            error=RuntimeError(provider_sentinel),
        )
    )

    async def fail_commit() -> None:
        raise RuntimeError(persistence_sentinel)

    monkeypatch.setattr(session, "commit", fail_commit)

    with caplog.at_level(logging.ERROR):
        with pytest.raises(RuntimeError) as exc_info:
            await phone_provisioning_job(
                {"session_factory": lambda: FakePhoneProvisioningSessionContext(session)},
                {"user_id": "00000000-0000-0000-0000-000000000123"},
            )

    assert_exception_state_is_sanitized(
        exc_info.value,
        provider_sentinel,
        persistence_sentinel,
    )
    assert provider_sentinel not in caplog.text
    assert persistence_sentinel not in caplog.text
