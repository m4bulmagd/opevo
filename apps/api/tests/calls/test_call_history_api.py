from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from livekit import api
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.agent_config import AgentConfig
from app.models.call import Call
from app.models.call_message import CallMessage
from app.models.phone_number import PhoneNumber
from app.models.user import User
from app.providers.livekit_recording.livekit import LiveKitRecordingProvider
from app.providers.storage.base import StorageProviderError
from app.providers.storage.s3 import S3Storage
from app.repositories.call_repository import CallRepository
from app.repositories.message_repository import MessageRepository
from app.schemas.agent_runtime import TranscriptAppendRequest
from app.services.call_lifecycle_service import CallLifecycleService
from app.services.call_history_service import CallDeleteRetryableError, CallHistoryService
from app.services.livekit_recording_service import LiveKitRecordingService
from app.services.recording_service import (
    RecordingDeleteRetryableError,
    RecordingService,
)
from app.services.transcript_service import TranscriptCallNotFoundError, TranscriptService


class FakeRecordingService:
    async def get_access_url(
        self,
        *,
        call_id: UUID,
        user_id: UUID,
        recording_object_key: str | None,
    ) -> None:
        return None


class RecordingStorage:
    def __init__(self) -> None:
        self.delete_object = AsyncMock()


class OrderedRecordingStorage:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def delete_object(self, *, object_key: str) -> None:
        self.events.append(f"delete_object:{object_key}")


class RecordingEgressStopper:
    def __init__(self, events: list[str], *, failure: Exception | None = None) -> None:
        self.events = events
        self.failure = failure

    async def ensure_not_running(self, egress_id: str) -> None:
        self.events.append(f"ensure_not_running:{egress_id}")
        if self.failure is not None:
            raise self.failure


class StatusRecordingEgressClient:
    def __init__(self, status: int, events: list[str]) -> None:
        self.status = status
        self.events = events
        self.stop_requests: list[object] = []

    async def list_egress(self, request):
        self.events.append(f"ensure_not_running:{request.egress_id}")
        return SimpleNamespace(
            items=[
                SimpleNamespace(
                    egress_id=request.egress_id,
                    status=self.status,
                )
            ]
        )

    async def stop_egress(self, request) -> None:
        self.stop_requests.append(request)


async def seed_call_history(
    database_url: str,
    *,
    clerk_user_id: str,
    email: str,
) -> dict[str, UUID]:
    engine = create_async_engine(database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        user = User(clerk_user_id=clerk_user_id, email=email)
        session.add(user)
        await session.flush()

        base_time = datetime(2026, 3, 28, 10, 0, tzinfo=UTC)
        newest_call = Call(
            user_id=user.id,
            caller_number="+33111111111",
            status="completed",
            started_at=base_time + timedelta(minutes=2),
            ended_at=base_time + timedelta(minutes=3),
            duration_seconds=60,
            minutes_charged=1,
            summary_text="Newest call",
        )
        older_call = Call(
            user_id=user.id,
            caller_number="+33222222222",
            status="completed",
            started_at=base_time,
            ended_at=base_time + timedelta(minutes=1),
            duration_seconds=60,
            minutes_charged=1,
            summary_text="Older call",
        )
        deleted_call = Call(
            user_id=user.id,
            caller_number="+33333333333",
            status="completed",
            started_at=base_time + timedelta(minutes=1),
            ended_at=base_time + timedelta(minutes=2),
            duration_seconds=60,
            minutes_charged=1,
            summary_text="Deleted call",
            deleted_at=base_time + timedelta(minutes=5),
        )
        session.add_all([newest_call, older_call, deleted_call])
        await session.commit()
        result = {
            "newest_id": newest_call.id,
            "older_id": older_call.id,
            "deleted_id": deleted_call.id,
        }
    await engine.dispose()
    return result


async def seed_user(database_url: str, *, clerk_user_id: str, email: str) -> None:
    engine = create_async_engine(database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        session.add(User(clerk_user_id=clerk_user_id, email=email))
        await session.commit()
    await engine.dispose()


async def seed_call_with_transcript(
    database_url: str,
    *,
    clerk_user_id: str,
    email: str,
    deleted: bool = False,
    status: str = "completed",
) -> UUID:
    engine = create_async_engine(database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        user = User(clerk_user_id=clerk_user_id, email=email)
        session.add(user)
        await session.flush()

        call = Call(
            user_id=user.id,
            caller_number="+33123456789",
            status=status,
            started_at=datetime(2026, 3, 28, 10, 0, tzinfo=UTC),
            ended_at=datetime(2026, 3, 28, 10, 1, tzinfo=UTC),
            duration_seconds=60,
            minutes_charged=1,
            summary_text="Caller request: Opening hours.",
            deleted_at=datetime(2026, 3, 28, 10, 2, tzinfo=UTC) if deleted else None,
        )
        session.add(call)
        await session.flush()
        session.add_all(
            [
                CallMessage(
                    call_id=call.id,
                    speaker="AGENT",
                    text="We open at 9 AM.",
                    sequence_number=2,
                ),
                CallMessage(
                    call_id=call.id,
                    speaker="CALLER",
                    text="What are your opening hours?",
                    sequence_number=1,
                ),
                CallMessage(
                    call_id=call.id,
                    speaker="AGENT",
                    text="Can I help with anything else?",
                    sequence_number=3,
                ),
            ]
        )
        await session.commit()
        call_id = call.id
    await engine.dispose()
    return call_id


async def seed_call_with_recording(
    database_url: str,
    *,
    clerk_user_id: str,
    email: str,
    recording_url: str,
    recording_object_key: str | None = None,
    recording_egress_id: str | None = None,
) -> UUID:
    engine = create_async_engine(database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        user = User(clerk_user_id=clerk_user_id, email=email)
        session.add(user)
        await session.flush()

        call = Call(
            user_id=user.id,
            caller_number="+33123456789",
            status="completed",
            started_at=datetime(2026, 3, 28, 10, 0, tzinfo=UTC),
            ended_at=datetime(2026, 3, 28, 10, 1, tzinfo=UTC),
            duration_seconds=60,
            minutes_charged=1,
            summary_text="Caller request: Opening hours.",
            recording_url=recording_url,
            recording_object_key=recording_object_key,
            recording_egress_id=recording_egress_id,
        )
        session.add(call)
        await session.commit()
        call_id = call.id
    await engine.dispose()
    return call_id


async def fetch_call(database_url: str, *, call_id: UUID) -> Call:
    engine = create_async_engine(database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        call = await session.get(Call, call_id)
        assert call is not None
    await engine.dispose()
    return call


async def seed_call_into_session(
    session,
    *,
    clerk_user_id: str = "user_calls",
    email: str = "calls@example.com",
    recording_url: str | None = None,
    recording_object_key: str | None = None,
    status: str = "completed",
    failure_code: str | None = None,
    summary_text: str | None = "Caller request: Opening hours.",
    summary_data: object = None,
) -> Call:
    user = User(clerk_user_id=clerk_user_id, email=email)
    session.add(user)
    await session.flush()

    session.add(
        AgentConfig(
            user_id=user.id,
            agent_name="Ava",
            system_prompt="Be helpful",
            knowledge_base="Hours 9-5",
            pipeline_mode="stt_llm_tts",
            is_enabled=True,
        )
    )
    session.add(
        PhoneNumber(
            user_id=user.id,
            e164="+33999888777",
            country_code="FR",
            provider="telnyx",
            provider_number_id="tnx_123",
            provider_connection_name="app-active",
            is_active=True,
        )
    )

    call = Call(
        user_id=user.id,
        caller_number="+33123456789",
        status=status,
        failure_code=failure_code,
        started_at=datetime(2026, 3, 28, 10, 0, tzinfo=UTC),
        ended_at=datetime(2026, 3, 28, 10, 1, tzinfo=UTC),
        duration_seconds=60,
        minutes_charged=1,
        summary_text=summary_text,
        summary_data=summary_data,
        recording_url=recording_url,
        recording_object_key=recording_object_key,
    )
    session.add(call)
    await session.commit()
    return call


@pytest.mark.anyio
async def test_list_calls_returns_visible_calls_newest_first(
    async_client, client_database_url, rs256_clerk_token_for
) -> None:
    ids = await seed_call_history(
        client_database_url,
        clerk_user_id="user_calls",
        email="calls@example.com",
    )

    response = await async_client.get(
        "/api/calls",
        headers={"authorization": f"Bearer {rs256_clerk_token_for('user_calls')}"},
    )

    assert response.status_code == 200
    assert [UUID(item["id"]) for item in response.json()["calls"]] == [
        ids["newest_id"],
        ids["older_id"],
    ]


@pytest.mark.anyio
async def test_list_and_detail_expose_bounded_structured_summary(db_session) -> None:
    summary_data = {
        "summary_text": "Caller wants to arrange an appointment.",
        "caller_intent": "Book a consultation",
        "action_items": ["Return the call"],
        "sentiment": "positive",
        "follow_up_required": True,
        "provider_debug_payload": "must not be exposed",
    }
    call = await seed_call_into_session(
        db_session,
        summary_text="Caller wants to arrange an appointment.",
        summary_data=summary_data,
    )
    service = CallHistoryService(db_session, recording_service=FakeRecordingService())

    list_item = (await service.list_calls(call.user_id))[0]
    detail = await service.get_call_detail(call.user_id, call.id)

    for response in (list_item, detail):
        assert response.summary_status == "ready"
        assert response.caller_intent == "Book a consultation"
        assert response.action_items == ["Return the call"]
        assert response.sentiment == "positive"
        assert response.follow_up_required is True
        assert "provider_debug_payload" not in response.model_dump()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("status", "failure_code", "summary_text", "summary_data", "expected_status"),
    [
        ("finalizing", None, None, None, "processing"),
        ("completed", None, None, None, "unavailable"),
        ("failed", "legacy_failure", None, None, "unavailable"),
        ("completed", None, "Legacy text summary", "not-json", "ready"),
        (
            "finalizing",
            None,
            "Summary wins over active-state processing",
            None,
            "ready",
        ),
    ],
)
async def test_summary_status_is_server_authoritative_with_ready_precedence(
    db_session,
    status: str,
    failure_code: str | None,
    summary_text: str | None,
    summary_data: object,
    expected_status: str,
) -> None:
    call = await seed_call_into_session(
        db_session,
        status=status,
        failure_code=failure_code,
        summary_text=summary_text,
        summary_data=summary_data,
    )

    item = (
        await CallHistoryService(
            db_session,
            recording_service=FakeRecordingService(),
        ).list_calls(call.user_id)
    )[0]

    assert item.summary_status == expected_status
    assert item.caller_intent is None
    assert item.action_items is None
    assert item.sentiment is None
    assert item.follow_up_required is None


@pytest.mark.anyio
async def test_has_recording_uses_private_object_key_not_legacy_url(db_session) -> None:
    call = await seed_call_into_session(
        db_session,
        recording_url="https://stored.example.com/legacy-public-url",
        recording_object_key=None,
    )

    item = (
        await CallHistoryService(
            db_session,
            recording_service=FakeRecordingService(),
        ).list_calls(call.user_id)
    )[0]

    assert item.has_recording is False


@pytest.mark.anyio
async def test_recording_service_deletes_by_private_object_key() -> None:
    storage = RecordingStorage()
    service = RecordingService(provider=storage)

    await service.delete_recording(
        call_id=UUID("00000000-0000-0000-0000-000000000001"),
        recording_object_key="calls/user_calls/call.mp3",
    )

    storage.delete_object.assert_awaited_once_with(
        object_key="calls/user_calls/call.mp3"
    )


@pytest.mark.anyio
async def test_delete_call_stops_egress_then_deletes_object_then_purges_content(
    db_session,
    monkeypatch,
) -> None:
    events: list[str] = []
    storage = OrderedRecordingStorage(events)
    recording_service = RecordingService(
        provider=storage,
        egress_stopper=RecordingEgressStopper(events),
    )
    call = await seed_call_into_session(
        db_session,
        recording_url="https://stored.example.com/legacy",
        recording_object_key="calls/user_calls/call.mp3",
        summary_text="Caller wants to arrange an appointment.",
        summary_data={
            "summary_text": "Caller wants to arrange an appointment.",
            "caller_intent": "Book a consultation",
            "action_items": ["Return the call"],
            "sentiment": "positive",
            "follow_up_required": True,
        },
    )
    call.summary_transcript_max_sequence = 1
    call.recording_egress_id = "egress-1"
    db_session.add(
        CallMessage(
            call_id=call.id,
            speaker="CALLER",
            text="Please call me back.",
            sequence_number=1,
        )
    )
    await db_session.commit()

    original_purge = CallRepository.purge_customer_content

    async def track_purge(repository, purged_call):
        events.append("purge")
        return await original_purge(repository, purged_call)

    monkeypatch.setattr(CallRepository, "purge_customer_content", track_purge)

    await CallHistoryService(
        db_session,
        recording_service=recording_service,
    ).delete_call(call.user_id, call.id)

    assert events == [
        "ensure_not_running:egress-1",
        "delete_object:calls/user_calls/call.mp3",
        "purge",
    ]
    assert await MessageRepository(db_session).list_by_call_id(call.id) == []
    await db_session.refresh(call)
    assert call.caller_number is None
    assert call.summary_text is None
    assert call.summary_data is None
    assert call.summary_transcript_max_sequence is None
    assert call.recording_object_key is None
    assert call.recording_url is None
    assert call.recording_egress_id is None
    assert call.deleted_at is not None


@pytest.mark.anyio
@pytest.mark.parametrize(
    "terminal_status",
    [
        api.EgressStatus.EGRESS_FAILED,
        api.EgressStatus.EGRESS_ABORTED,
    ],
)
async def test_delete_call_accepts_failed_terminal_egress_before_storage_and_purge(
    db_session,
    monkeypatch,
    terminal_status: int,
) -> None:
    events: list[str] = []
    egress_client = StatusRecordingEgressClient(terminal_status, events)
    egress_service = LiveKitRecordingService(
        provider=LiveKitRecordingProvider(
            egress_client=egress_client,
            bucket_name="recordings",
            endpoint_url="http://minio:9000",
            access_key="key",
            secret_key="secret",
            region="us-east-1",
        )
    )
    recording_service = RecordingService(
        provider=OrderedRecordingStorage(events),
        egress_stopper=egress_service,
    )
    call = await seed_call_into_session(
        db_session,
        recording_object_key="calls/user_calls/terminal.mp3",
    )
    call.recording_egress_id = "egress-terminal"
    await db_session.commit()

    original_purge = CallRepository.purge_customer_content

    async def track_purge(repository, purged_call):
        events.append("purge")
        return await original_purge(repository, purged_call)

    monkeypatch.setattr(CallRepository, "purge_customer_content", track_purge)

    await CallHistoryService(
        db_session,
        recording_service=recording_service,
    ).delete_call(call.user_id, call.id)

    assert events == [
        "ensure_not_running:egress-terminal",
        "delete_object:calls/user_calls/terminal.mp3",
        "purge",
    ]
    assert egress_client.stop_requests == []
    await db_session.refresh(call)
    assert call.recording_egress_id is None
    assert call.recording_object_key is None
    assert call.deleted_at is not None


@pytest.mark.anyio
async def test_delete_call_egress_stop_failure_leaves_storage_and_database_unchanged(
    db_session,
) -> None:
    events: list[str] = []
    recording_service = RecordingService(
        provider=OrderedRecordingStorage(events),
        egress_stopper=RecordingEgressStopper(
            events,
            failure=RuntimeError("egress stop uncertain"),
        ),
    )
    call = await seed_call_into_session(
        db_session,
        recording_url="https://stored.example.com/legacy",
        recording_object_key="calls/user_calls/retry-egress.mp3",
        summary_text="Keep content until egress stop is proven.",
    )
    call.recording_egress_id = "egress-retry"
    db_session.add(
        CallMessage(
            call_id=call.id,
            speaker="CALLER",
            text="Keep this transcript for the retry.",
            sequence_number=1,
        )
    )
    await db_session.commit()

    with pytest.raises(CallDeleteRetryableError):
        await CallHistoryService(
            db_session,
            recording_service=recording_service,
        ).delete_call(call.user_id, call.id)

    assert events == ["ensure_not_running:egress-retry"]
    await db_session.refresh(call)
    assert call.deleted_at is None
    assert call.caller_number == "+33123456789"
    assert call.summary_text == "Keep content until egress stop is proven."
    assert call.recording_object_key == "calls/user_calls/retry-egress.mp3"
    assert call.recording_egress_id == "egress-retry"
    assert len(await MessageRepository(db_session).list_by_call_id(call.id)) == 1


@pytest.mark.anyio
async def test_delete_call_storage_failure_keeps_customer_content_for_retry(
    db_session,
) -> None:
    storage = RecordingStorage()
    storage.delete_object.side_effect = StorageProviderError(
        "provider_retryable",
        error_class="unavailable",
    )
    call = await seed_call_into_session(
        db_session,
        recording_url="https://stored.example.com/legacy",
        recording_object_key="calls/user_calls/retry.mp3",
        summary_text="Keep this visible until storage deletion succeeds.",
        summary_data={
            "summary_text": "Keep this visible until storage deletion succeeds.",
            "caller_intent": "Request a callback",
            "action_items": ["Return the call"],
            "sentiment": "neutral",
            "follow_up_required": True,
        },
    )
    db_session.add(
        CallMessage(
            call_id=call.id,
            speaker="CALLER",
            text="Keep this transcript for the retry.",
            sequence_number=1,
        )
    )
    await db_session.commit()

    with pytest.raises(Exception) as exc_info:
        await CallHistoryService(
            db_session,
            recording_service=RecordingService(provider=storage),
        ).delete_call(call.user_id, call.id)

    await db_session.refresh(call)
    assert call.deleted_at is None
    assert call.caller_number == "+33123456789"
    assert call.summary_text == "Keep this visible until storage deletion succeeds."
    assert call.recording_object_key == "calls/user_calls/retry.mp3"
    assert len(await MessageRepository(db_session).list_by_call_id(call.id)) == 1
    assert type(exc_info.value).__name__ == "CallDeleteRetryableError"


@pytest.mark.anyio
async def test_delete_call_treats_missing_recording_as_success(db_session) -> None:
    storage = RecordingStorage()
    storage.delete_object.side_effect = FileNotFoundError("recording already absent")
    call = await seed_call_into_session(
        db_session,
        recording_object_key="calls/user_calls/missing.mp3",
    )

    await CallHistoryService(
        db_session,
        recording_service=RecordingService(provider=storage),
    ).delete_call(call.user_id, call.id)

    await db_session.refresh(call)
    assert call.deleted_at is not None
    assert call.recording_object_key is None


@pytest.mark.anyio
async def test_delete_call_is_idempotent_without_repeating_storage_delete(
    db_session,
) -> None:
    storage = RecordingStorage()
    call = await seed_call_into_session(
        db_session,
        recording_object_key="calls/user_calls/once.mp3",
    )
    service = CallHistoryService(
        db_session,
        recording_service=RecordingService(provider=storage),
    )

    await service.delete_call(call.user_id, call.id)
    await service.delete_call(call.user_id, call.id)

    storage.delete_object.assert_awaited_once_with(
        object_key="calls/user_calls/once.mp3"
    )


@pytest.mark.anyio
async def test_deleted_call_rejects_delayed_transcript_and_lifecycle_writers(
    db_session,
) -> None:
    call = await seed_call_into_session(db_session)
    await CallHistoryService(
        db_session,
        recording_service=RecordingService(provider=RecordingStorage()),
    ).delete_call(call.user_id, call.id)

    with pytest.raises(TranscriptCallNotFoundError):
        await TranscriptService(db_session).merge_recovery(
            call_id=call.id,
            transcript=[
                TranscriptAppendRequest(
                    sequence_number=1,
                    speaker="CALLER",
                    text="Delayed transcript must not return",
                )
            ],
        )
    with pytest.raises(ValueError, match="Call not found"):
        await CallLifecycleService(db_session).end_from_agent(
            call_id=call.id,
            duration_seconds=60,
        )

    assert await MessageRepository(db_session).list_by_call_id(call.id) == []
    await db_session.refresh(call)
    assert call.summary_text is None
    assert call.summary_data is None
    assert call.recording_object_key is None
    assert call.recording_url is None
    assert call.recording_egress_id is None


@pytest.mark.anyio
async def test_deleted_calls_are_excluded_from_specialized_mutating_selectors(
    db_session,
    active_user,
) -> None:
    now = datetime.now(UTC)
    call = Call(
        user_id=active_user.id,
        livekit_room_id="deleted-pending-room",
        status="pending",
        state_changed_at=now - timedelta(hours=1),
        deleted_at=now,
    )
    db_session.add(call)
    await db_session.commit()
    repository = CallRepository(db_session)

    assert (
        await repository.get_pending_by_room_without_recording(
            room_name="deleted-pending-room"
        )
        is None
    )
    assert await repository.connect_if_pending(call_id=call.id) is None
    assert call.id not in await repository.list_stale_pending_ids(
        stale_before=now,
        limit=10,
    )
    call.status = "connected"
    call.livekit_room_id = "deleted-connected-room"
    await db_session.commit()
    assert (
        await repository.get_by_id_without_recording_for_update(call_id=call.id)
        is None
    )
    claimed = await repository.claim_stale_reconciliation_rows(
        connected_before=now,
        ending_before=now,
        finalizing_before=now,
        limit=10,
    )
    assert call.id not in {claimed_call.id for claimed_call in claimed}


@pytest.mark.anyio
async def test_get_call_detail_returns_transcript(
    async_client, client_database_url, rs256_clerk_token_for
) -> None:
    call_id = await seed_call_with_transcript(
        client_database_url,
        clerk_user_id="user_calls",
        email="calls@example.com",
    )

    response = await async_client.get(
        f"/api/calls/{call_id}",
        headers={"authorization": f"Bearer {rs256_clerk_token_for('user_calls')}"},
    )

    assert response.status_code == 200
    assert [line["sequence_number"] for line in response.json()["transcript"]] == [1, 2, 3]


@pytest.mark.anyio
async def test_get_call_detail_returns_404_for_soft_deleted_call(
    async_client, client_database_url, rs256_clerk_token_for
) -> None:
    call_id = await seed_call_with_transcript(
        client_database_url,
        clerk_user_id="user_calls",
        email="calls@example.com",
        deleted=True,
    )

    response = await async_client.get(
        f"/api/calls/{call_id}",
        headers={"authorization": f"Bearer {rs256_clerk_token_for('user_calls')}"},
    )

    assert response.status_code == 404


@pytest.mark.anyio
async def test_get_call_detail_mints_fresh_recording_url(
    async_client, client_database_url, rs256_clerk_token_for, monkeypatch
) -> None:
    async def fake_get_access_url(self, *, call_id, user_id, recording_object_key):
        assert recording_object_key == "calls/user_calls/call.mp3"
        return "https://signed.example.com/fresh"

    call_id = await seed_call_with_recording(
        client_database_url,
        clerk_user_id="user_calls",
        email="calls@example.com",
        recording_url="https://stored.example.com/old",
        recording_object_key="calls/user_calls/call.mp3",
    )

    from app.services.recording_service import RecordingService

    monkeypatch.setattr(RecordingService, "get_access_url", fake_get_access_url)

    response = await async_client.get(
        f"/api/calls/{call_id}",
        headers={"authorization": f"Bearer {rs256_clerk_token_for('user_calls')}"},
    )

    assert response.status_code == 200
    assert response.json()["recording_url"] == "https://signed.example.com/fresh"


@pytest.mark.anyio
async def test_get_call_detail_mints_fresh_recording_url_from_object_key(
    db_session
) -> None:
    async def fake_get_access_url(self, *, call_id, user_id, recording_object_key):
        assert recording_object_key == "calls/user_calls/object-key.mp3"
        return "https://signed.example.com/fresh"

    call = await seed_call_into_session(
        db_session,
        clerk_user_id="user_calls",
        email="calls@example.com",
        recording_url="https://stored.example.com/old",
        recording_object_key="calls/user_calls/object-key.mp3",
    )

    class FakeRecordingService:
        async def get_access_url(self, *, call_id, user_id, recording_object_key):
            return await fake_get_access_url(
                self,
                call_id=call_id,
                user_id=user_id,
                recording_object_key=recording_object_key,
            )

    detail = await CallHistoryService(
        db_session,
        recording_service=FakeRecordingService(),
    ).get_call_detail(
        call.user_id,
        call.id,
    )

    assert detail.recording_url == "https://signed.example.com/fresh"


@pytest.mark.anyio
async def test_get_call_detail_returns_null_recording_url_when_object_missing(
    db_session
) -> None:
    class FakeMissingRecordingService:
        async def get_access_url(self, *, call_id, user_id, recording_object_key):
            assert recording_object_key == "calls/user_calls/object-key.mp3"
            raise FileNotFoundError("recording object missing")

    call = await seed_call_into_session(
        db_session,
        clerk_user_id="user_calls",
        email="calls@example.com",
        recording_url="https://stored.example.com/old",
        recording_object_key="calls/user_calls/object-key.mp3",
    )

    detail = await CallHistoryService(
        db_session,
        recording_service=FakeMissingRecordingService(),
    ).get_call_detail(
        call.user_id,
        call.id,
    )

    assert detail.recording_url is None


@pytest.mark.anyio
async def test_get_call_detail_returns_null_recording_url_without_recording(
    async_client, client_database_url, rs256_clerk_token_for
) -> None:
    call_id = await seed_call_with_transcript(
        client_database_url,
        clerk_user_id="user_calls",
        email="calls@example.com",
    )

    response = await async_client.get(
        f"/api/calls/{call_id}",
        headers={"authorization": f"Bearer {rs256_clerk_token_for('user_calls')}"},
    )

    assert response.status_code == 200
    assert response.json()["recording_url"] is None


@pytest.mark.anyio
async def test_delete_call_soft_deletes_and_hides_it(
    async_client, client_database_url, rs256_clerk_token_for
) -> None:
    call_id = await seed_call_with_transcript(
        client_database_url,
        clerk_user_id="user_calls",
        email="calls@example.com",
    )

    delete_response = await async_client.delete(
        f"/api/calls/{call_id}",
        headers={"authorization": f"Bearer {rs256_clerk_token_for('user_calls')}"},
    )
    detail_response = await async_client.get(
        f"/api/calls/{call_id}",
        headers={"authorization": f"Bearer {rs256_clerk_token_for('user_calls')}"},
    )

    refreshed_call = await fetch_call(client_database_url, call_id=call_id)

    assert delete_response.status_code == 204
    assert detail_response.status_code == 404
    assert refreshed_call.deleted_at is not None


@pytest.mark.anyio
async def test_delete_call_rejects_active_call_without_mutating_customer_content(
    async_client,
    client_database_url,
    rs256_clerk_token_for,
    monkeypatch,
) -> None:
    delete_attempts: list[str | None] = []

    async def track_delete(
        self, *, call_id, recording_object_key, recording_egress_id
    ):
        delete_attempts.append(recording_object_key)

    monkeypatch.setattr(RecordingService, "delete_recording", track_delete)
    call_id = await seed_call_with_transcript(
        client_database_url,
        clerk_user_id="user_calls",
        email="calls@example.com",
        status="connected",
    )
    headers = {"authorization": f"Bearer {rs256_clerk_token_for('user_calls')}"}

    response = await async_client.delete(f"/api/calls/{call_id}", headers=headers)
    detail_response = await async_client.get(f"/api/calls/{call_id}", headers=headers)
    refreshed_call = await fetch_call(client_database_url, call_id=call_id)

    assert response.status_code == 409
    assert response.json() == {"detail": {"code": "call_delete_active"}}
    assert delete_attempts == []
    assert detail_response.status_code == 200
    assert len(detail_response.json()["transcript"]) == 3
    assert refreshed_call.deleted_at is None
    assert refreshed_call.caller_number == "+33123456789"
    assert refreshed_call.summary_text == "Caller request: Opening hours."


@pytest.mark.anyio
async def test_delete_call_returns_404_for_other_users_call(
    async_client, client_database_url, rs256_clerk_token_for
) -> None:
    await seed_user(
        client_database_url,
        clerk_user_id="user_calls",
        email="calls@example.com",
    )
    foreign_call_id = await seed_call_with_transcript(
        client_database_url,
        clerk_user_id="user_other",
        email="other@example.com",
    )

    response = await async_client.delete(
        f"/api/calls/{foreign_call_id}",
        headers={"authorization": f"Bearer {rs256_clerk_token_for('user_calls')}"},
    )

    assert response.status_code == 404


@pytest.mark.anyio
async def test_delete_call_returns_404_for_unknown_call(
    async_client, client_database_url, rs256_clerk_token_for
) -> None:
    await seed_user(
        client_database_url,
        clerk_user_id="user_calls",
        email="calls@example.com",
    )

    response = await async_client.delete(
        "/api/calls/00000000-0000-0000-0000-000000000001",
        headers={"authorization": f"Bearer {rs256_clerk_token_for('user_calls')}"},
    )

    assert response.status_code == 404


@pytest.mark.anyio
async def test_delete_call_repeat_owner_returns_204_without_repeating_storage_delete(
    async_client,
    client_database_url,
    rs256_clerk_token_for,
    monkeypatch,
) -> None:
    deleted_object_keys: list[str | None] = []

    async def track_delete(
        self, *, call_id, recording_object_key, recording_egress_id
    ):
        deleted_object_keys.append(recording_object_key)

    monkeypatch.setattr(RecordingService, "delete_recording", track_delete)
    call_id = await seed_call_with_recording(
        client_database_url,
        clerk_user_id="user_calls",
        email="calls@example.com",
        recording_url="https://stored.example.com/legacy",
        recording_object_key="calls/user_calls/delete-once.mp3",
    )
    headers = {"authorization": f"Bearer {rs256_clerk_token_for('user_calls')}"}

    first_response = await async_client.delete(f"/api/calls/{call_id}", headers=headers)
    repeat_response = await async_client.delete(f"/api/calls/{call_id}", headers=headers)

    assert first_response.status_code == 204
    assert repeat_response.status_code == 204
    assert deleted_object_keys == ["calls/user_calls/delete-once.mp3"]


@pytest.mark.anyio
async def test_delete_call_returns_safe_retryable_error_without_purging_content(
    async_client,
    client_database_url,
    rs256_clerk_token_for,
    monkeypatch,
) -> None:
    async def fail_delete(
        self, *, call_id, recording_object_key, recording_egress_id
    ):
        raise RecordingDeleteRetryableError

    monkeypatch.setattr(RecordingService, "delete_recording", fail_delete)
    call_id = await seed_call_with_recording(
        client_database_url,
        clerk_user_id="user_calls",
        email="calls@example.com",
        recording_url="https://stored.example.com/legacy",
        recording_object_key="calls/user_calls/retry.mp3",
    )

    response = await async_client.delete(
        f"/api/calls/{call_id}",
        headers={"authorization": f"Bearer {rs256_clerk_token_for('user_calls')}"},
    )
    refreshed_call = await fetch_call(client_database_url, call_id=call_id)

    assert response.status_code == 503
    assert response.json() == {"detail": {"code": "call_delete_retryable"}}
    assert refreshed_call.deleted_at is None
    assert refreshed_call.summary_text == "Caller request: Opening hours."
    assert refreshed_call.caller_number == "+33123456789"
    assert refreshed_call.recording_object_key == "calls/user_calls/retry.mp3"


@pytest.mark.anyio
async def test_delete_call_returns_safe_retryable_error_when_egress_stop_is_uncertain(
    async_client,
    client_database_url,
    rs256_clerk_token_for,
    monkeypatch,
) -> None:
    egress_attempts: list[str] = []
    storage_attempts: list[str] = []

    async def fail_stop(self, egress_id):
        egress_attempts.append(egress_id)
        raise RuntimeError("egress state unavailable")

    async def track_storage_delete(self, *, object_key):
        storage_attempts.append(object_key)

    monkeypatch.setattr(
        LiveKitRecordingService,
        "ensure_not_running",
        fail_stop,
        raising=False,
    )
    monkeypatch.setattr(S3Storage, "delete_object", track_storage_delete)
    call_id = await seed_call_with_recording(
        client_database_url,
        clerk_user_id="user_calls",
        email="calls@example.com",
        recording_url="https://stored.example.com/legacy",
        recording_object_key="calls/user_calls/retry-egress.mp3",
        recording_egress_id="egress-retry",
    )

    response = await async_client.delete(
        f"/api/calls/{call_id}",
        headers={"authorization": f"Bearer {rs256_clerk_token_for('user_calls')}"},
    )
    refreshed_call = await fetch_call(client_database_url, call_id=call_id)

    assert response.status_code == 503
    assert response.json() == {"detail": {"code": "call_delete_retryable"}}
    assert egress_attempts == ["egress-retry"]
    assert storage_attempts == []
    assert refreshed_call.deleted_at is None
    assert refreshed_call.summary_text == "Caller request: Opening hours."
    assert refreshed_call.caller_number == "+33123456789"
    assert refreshed_call.recording_object_key == "calls/user_calls/retry-egress.mp3"
    assert refreshed_call.recording_egress_id == "egress-retry"
