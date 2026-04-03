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
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.call import Call
from app.models.call_message import CallMessage
from app.models.notification import Notification
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
