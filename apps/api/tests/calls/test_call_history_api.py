from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.agent_config import AgentConfig
from app.models.call import Call
from app.models.call_message import CallMessage
from app.models.phone_number import PhoneNumber
from app.models.outbox_event import OutboxEvent
from app.models.recording_egress_operation import RecordingEgressOperation
from app.models.user import User
from app.repositories.call_repository import CallRepository
from app.repositories.message_repository import MessageRepository
from presvo_contracts import TranscriptSegment as TranscriptAppendRequest
from app.services.call_lifecycle_service import CallLifecycleService
from app.services.call_history_service import CallHistoryService
from app.services.recording_service import RecordingService
from app.services.recording_lifecycle_service import RecordingLifecycleService
from app.services.transcript_service import TranscriptCallNotFoundError, TranscriptService
from app.routers import calls as calls_router


class FakeRecordingService:
    async def get_access_url(
        self,
        *,
        call_id: UUID,
        user_id: UUID,
        recording_object_key: str | None,
    ) -> None:
        return None


async def seed_call_history(
    database_url: str,
    *,
    clerk_user_id: str,
    email: str,
    user_status: str = "active",
    newest_caller_number: str = "+33111111111",
) -> dict[str, UUID]:
    engine = create_async_engine(database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        user = User(
            clerk_user_id=clerk_user_id,
            email=email,
            status=user_status,
        )
        session.add(user)
        await session.flush()

        base_time = datetime(2026, 3, 28, 10, 0, tzinfo=UTC)
        newest_call = Call(
            user_id=user.id,
            caller_number=newest_caller_number,
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
    user_status: str = "active",
) -> UUID:
    engine = create_async_engine(database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        user = User(
            clerk_user_id=clerk_user_id,
            email=email,
            status=user_status,
        )
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
    user_status: str = "active",
) -> UUID:
    engine = create_async_engine(database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        user = User(
            clerk_user_id=clerk_user_id,
            email=email,
            status=user_status,
        )
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
    payload = response.json()
    assert [UUID(item["id"]) for item in payload["calls"]] == [
        ids["newest_id"],
        ids["older_id"],
    ]
    assert payload == {
        "calls": payload["calls"],
        "total": 2,
        "limit": 20,
        "offset": 0,
        "has_more": False,
    }


@pytest.mark.anyio
async def test_list_calls_applies_search_and_pagination_metadata(
    async_client,
    client_database_url,
    rs256_clerk_token_for,
) -> None:
    ids = await seed_call_history(
        client_database_url,
        clerk_user_id="user_calls_search",
        email="calls-search@example.invalid",
    )

    response = await async_client.get(
        "/api/calls?q=older&limit=1&offset=0",
        headers={
            "authorization": (
                f"Bearer {rs256_clerk_token_for('user_calls_search')}"
            )
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "calls": [response.json()["calls"][0]],
        "total": 1,
        "limit": 1,
        "offset": 0,
        "has_more": False,
    }
    assert UUID(response.json()["calls"][0]["id"]) == ids["older_id"]


@pytest.mark.anyio
async def test_list_calls_applies_status_and_date_range_query_contract(
    async_client,
    client_database_url,
    rs256_clerk_token_for,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ids = await seed_call_history(
        client_database_url,
        clerk_user_id="user_calls_filters",
        email="calls-filters@example.invalid",
    )
    original_list_calls = CallHistoryService.list_calls

    async def list_calls_at_fixed_time(
        service: CallHistoryService,
        user_id: UUID,
        **kwargs,
    ):
        return await original_list_calls(
            service,
            user_id,
            now=datetime(2026, 4, 1, tzinfo=UTC),
            **kwargs,
        )

    monkeypatch.setattr(
        CallHistoryService,
        "list_calls",
        list_calls_at_fixed_time,
    )

    response = await async_client.get(
        "/api/calls",
        params={
            "q": "older",
            "status": "completed",
            "range": "7d",
            "limit": 20,
            "offset": 0,
        },
        headers={
            "authorization": (
                f"Bearer {rs256_clerk_token_for('user_calls_filters')}"
            )
        },
    )

    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert UUID(response.json()["calls"][0]["id"]) == ids["older_id"]


@pytest.mark.anyio
async def test_list_calls_phone_search_matches_domestic_trunk_prefix_to_e164_number(
    async_client,
    client_database_url,
    rs256_clerk_token_for,
) -> None:
    ids = await seed_call_history(
        client_database_url,
        clerk_user_id="user_calls_domestic_phone_search",
        email="calls-domestic-phone-search@example.invalid",
        newest_caller_number="+33187001234",
    )

    response = await async_client.get(
        "/api/calls",
        params={"q": "01 87"},
        headers={
            "authorization": (
                "Bearer "
                f"{rs256_clerk_token_for('user_calls_domestic_phone_search')}"
            )
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert [UUID(item["id"]) for item in payload["calls"]] == [
        ids["newest_id"]
    ]
    assert payload["limit"] == 20
    assert payload["offset"] == 0
    assert payload["has_more"] is False


@pytest.mark.anyio
@pytest.mark.parametrize(
    "query_string",
    [
        "limit=0",
        "limit=101",
        "offset=-1",
        f"q={'x' * 101}",
        "status=connected",
        "range=14d",
    ],
)
async def test_list_calls_rejects_invalid_query_bounds(
    async_client,
    client_database_url,
    rs256_clerk_token_for,
    query_string,
) -> None:
    await seed_user(
        client_database_url,
        clerk_user_id="user_calls_bounds",
        email="calls-bounds@example.invalid",
    )
    response = await async_client.get(
        f"/api/calls?{query_string}",
        headers={
            "authorization": (
                f"Bearer {rs256_clerk_token_for('user_calls_bounds')}"
            )
        },
    )

    assert response.status_code == 422


@pytest.mark.anyio
async def test_inactive_owner_can_list_get_and_play_back_historical_call(
    async_client,
    client_database_url: str,
    rs256_clerk_token_for,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clerk_user_id = "inactive_call_history_owner"
    call_id = await seed_call_with_recording(
        client_database_url,
        clerk_user_id=clerk_user_id,
        email="inactive-call-history@example.invalid",
        recording_url="https://stored.example.invalid/old",
        recording_object_key="calls/inactive-owner/call.mp3",
        user_status="inactive",
    )
    await seed_user(
        client_database_url,
        clerk_user_id="inactive_call_history_other",
        email="inactive-call-history-other@example.invalid",
    )

    async def fake_get_access_url(
        self,
        *,
        call_id,
        user_id,
        recording_object_key,
    ):
        assert recording_object_key == "calls/inactive-owner/call.mp3"
        return "https://signed.example.invalid/playback"

    monkeypatch.setattr(
        RecordingService,
        "get_access_url",
        fake_get_access_url,
    )
    headers = {
        "Authorization": f"Bearer {rs256_clerk_token_for(clerk_user_id)}"
    }

    listed = await async_client.get("/api/calls", headers=headers)
    detail = await async_client.get(f"/api/calls/{call_id}", headers=headers)
    foreign_detail = await async_client.get(
        f"/api/calls/{call_id}",
        headers={
            "Authorization": (
                "Bearer "
                f"{rs256_clerk_token_for('inactive_call_history_other')}"
            )
        },
    )

    assert listed.status_code == 200
    assert [UUID(item["id"]) for item in listed.json()["calls"]] == [call_id]
    assert listed.json()["total"] == 1
    assert listed.json()["limit"] == 20
    assert listed.json()["offset"] == 0
    assert listed.json()["has_more"] is False
    assert detail.status_code == 200
    assert UUID(detail.json()["id"]) == call_id
    assert foreign_detail.status_code == 404
    assert (
        detail.json()["recording_url"]
        == "https://signed.example.invalid/playback"
    )


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

    list_item = (await service.list_calls(call.user_id)).calls[0]
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
    ).calls[0]

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
    ).calls[0]

    assert item.has_recording is False


@pytest.mark.anyio
async def test_delete_call_is_provider_free_and_persists_private_cleanup_intent(
    db_session,
) -> None:
    call = await seed_call_into_session(
        db_session,
        status="connected",
        recording_object_key="calls/owner/deleted-call.ogg",
        recording_url="https://legacy.invalid/deleted-call.ogg",
        summary_text="Private customer summary",
    )
    call.livekit_room_id = "room-delete-durable"
    call.recording_egress_id = "egress-delete-durable"
    lifecycle = RecordingLifecycleService(db_session)
    operation = await lifecycle.prepare_start(call)
    call_id = call.id
    user_id = call.user_id
    operation_id = operation.id
    call.status = "completed"
    db_session.add(
        CallMessage(
            call_id=call.id,
            speaker="CALLER",
            text="Private customer transcript",
            sequence_number=1,
        )
    )
    await db_session.commit()

    await CallHistoryService(
        db_session,
        recording_service=None,
        recording_lifecycle_service=lifecycle,
    ).delete_call(user_id, call_id)

    db_session.expire_all()
    deleted_call = await db_session.get(Call, call_id)
    stored_operation = await db_session.get(RecordingEgressOperation, operation_id)
    reconcile = await db_session.scalar(
        select(OutboxEvent).where(
            OutboxEvent.idempotency_key
            == f"recording.reconcile:{operation_id}:delete"
        )
    )
    assert deleted_call is not None
    assert deleted_call.deleted_at is not None
    assert deleted_call.caller_number is None
    assert deleted_call.summary_text is None
    assert deleted_call.summary_data is None
    assert deleted_call.recording_object_key is None
    assert deleted_call.recording_egress_id is None
    assert deleted_call.recording_url is None
    assert await MessageRepository(db_session).list_by_call_id(call_id) == []
    assert stored_operation is not None
    assert stored_operation.stop_requested_at is not None
    assert stored_operation.delete_requested_at is not None
    assert reconcile is not None
    assert reconcile.payload == {"operation_id": str(operation_id)}


@pytest.mark.anyio
async def test_delete_dependency_has_no_playback_or_provider_capability(db_session) -> None:
    service = calls_router.get_call_deletion_service(session=db_session)

    assert service.recording_service is None
    assert isinstance(
        service.recording_lifecycle_service,
        RecordingLifecycleService,
    )


@pytest.mark.anyio
async def test_delete_transaction_failure_rolls_back_intent_purge_and_tombstone(
    db_session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    call = await seed_call_into_session(
        db_session,
        status="connected",
        recording_object_key="calls/owner/rollback.ogg",
        summary_text="Keep after rollback",
    )
    call.livekit_room_id = "room-delete-rollback"
    operation = await RecordingLifecycleService(db_session).prepare_start(call)
    call.status = "completed"
    db_session.add(
        CallMessage(
            call_id=call.id,
            speaker="CALLER",
            text="Keep transcript after rollback",
            sequence_number=1,
        )
    )
    await db_session.commit()
    call_id = call.id
    user_id = call.user_id
    operation_id = operation.id
    start_event = await db_session.scalar(
        select(OutboxEvent).where(
            OutboxEvent.idempotency_key
            == f"recording.reconcile:{operation_id}:start"
        )
    )
    assert start_event is not None
    start_event_id = start_event.id
    original_start_due_at = start_event.next_attempt_at
    original_purge = CallRepository.purge_customer_content

    async def fail_after_local_purge(repository, purged_call):
        await original_purge(repository, purged_call)
        raise RuntimeError("forced local delete rollback")

    monkeypatch.setattr(
        CallRepository,
        "purge_customer_content",
        fail_after_local_purge,
    )

    with pytest.raises(RuntimeError, match="forced local delete rollback"):
        await CallHistoryService(
            db_session,
            recording_service=None,
            recording_lifecycle_service=RecordingLifecycleService(db_session),
        ).delete_call(user_id, call_id)

    db_session.expire_all()
    stored_call = await db_session.get(Call, call_id)
    stored_operation = await db_session.get(RecordingEgressOperation, operation_id)
    assert stored_call is not None
    assert stored_call.deleted_at is None
    assert stored_call.summary_text == "Keep after rollback"
    assert stored_call.recording_object_key == "calls/owner/rollback.ogg"
    assert len(await MessageRepository(db_session).list_by_call_id(call_id)) == 1
    assert stored_operation is not None
    assert stored_operation.stop_requested_at is None
    assert stored_operation.delete_requested_at is None
    stored_start_event = await db_session.get(OutboxEvent, start_event_id)
    assert stored_start_event is not None
    assert stored_start_event.status == "pending"
    assert stored_start_event.next_attempt_at == original_start_due_at
    assert await db_session.scalar(
        select(OutboxEvent).where(
            OutboxEvent.idempotency_key
            == f"recording.reconcile:{operation_id}:delete"
        )
    ) is None
    assert await db_session.scalar(
        select(OutboxEvent).where(
            OutboxEvent.idempotency_key
            == f"recording.reconcile:{operation_id}:stop"
        )
    ) is None


@pytest.mark.anyio
async def test_repeat_delete_repairs_missing_tombstone_cleanup_once(
    db_session,
) -> None:
    call = await seed_call_into_session(db_session, status="connected")
    call.livekit_room_id = "room-delete-repair"
    operation = await RecordingLifecycleService(db_session).prepare_start(call)
    call.status = "completed"
    await CallRepository(db_session).purge_customer_content(call)
    await db_session.commit()
    call_id = call.id
    user_id = call.user_id
    operation_id = operation.id
    service = CallHistoryService(
        db_session,
        recording_service=None,
        recording_lifecycle_service=RecordingLifecycleService(db_session),
    )

    await service.delete_call(user_id, call_id)
    await service.delete_call(user_id, call_id)

    db_session.expire_all()
    stored_operation = await db_session.get(RecordingEgressOperation, operation_id)
    assert stored_operation is not None
    assert stored_operation.stop_requested_at is not None
    assert stored_operation.delete_requested_at is not None
    assert await db_session.scalar(
        select(func.count())
        .select_from(OutboxEvent)
        .where(
            OutboxEvent.idempotency_key
            == f"recording.reconcile:{operation_id}:delete"
        )
    ) == 1


def test_recording_service_is_playback_only() -> None:
    assert [
        name
        for name, value in vars(RecordingService).items()
        if not name.startswith("_") and callable(value)
    ] == ["get_access_url"]


@pytest.mark.anyio
async def test_delete_call_without_recording_operation_is_still_idempotent(
    db_session,
) -> None:
    call = await seed_call_into_session(db_session)
    call_id = call.id
    user_id = call.user_id
    service = CallHistoryService(
        db_session,
        recording_service=None,
        recording_lifecycle_service=RecordingLifecycleService(db_session),
    )

    await service.delete_call(user_id, call_id)
    await service.delete_call(user_id, call_id)

    db_session.expire_all()
    stored = await db_session.get(Call, call_id)
    assert stored is not None
    assert stored.deleted_at is not None
    assert stored.recording_object_key is None
    assert await db_session.scalar(
        select(func.count())
        .select_from(RecordingEgressOperation)
        .where(RecordingEgressOperation.call_id == call_id)
    ) == 0


@pytest.mark.anyio
async def test_deleted_call_rejects_delayed_transcript_and_lifecycle_writers(
    db_session,
) -> None:
    call = await seed_call_into_session(db_session)
    await CallHistoryService(
        db_session,
        recording_service=None,
        recording_lifecycle_service=RecordingLifecycleService(db_session),
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
) -> None:
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
    assert detail_response.status_code == 200
    assert len(detail_response.json()["transcript"]) == 3
    assert refreshed_call.deleted_at is None
    assert refreshed_call.caller_number == "+33123456789"
    assert refreshed_call.summary_text == "Caller request: Opening hours."


@pytest.mark.parametrize(
    ("user_status", "expected_code"),
    [
        ("deactivating", "account_deactivating"),
        ("inactive", "account_inactive"),
    ],
)
@pytest.mark.anyio
async def test_delete_call_rejects_non_active_owner_without_destructive_side_effects(
    async_client,
    client_database_url,
    rs256_clerk_token_for,
    user_status: str,
    expected_code: str,
) -> None:
    call_id = await seed_call_with_recording(
        client_database_url,
        clerk_user_id=f"{user_status}-delete-owner",
        email=f"{user_status}-delete-owner@example.com",
        recording_url="https://stored.example.com/private",
        recording_object_key=f"calls/{user_status}/retained.ogg",
        recording_egress_id=f"egress-{user_status}",
        user_status=user_status,
    )

    response = await async_client.delete(
        f"/api/calls/{call_id}",
        headers={
            "authorization": (
                f"Bearer {rs256_clerk_token_for(f'{user_status}-delete-owner')}"
            )
        },
    )
    stored = await fetch_call(client_database_url, call_id=call_id)

    assert response.status_code == 409
    assert response.json() == {"detail": {"code": expected_code}}
    assert stored.deleted_at is None
    assert stored.recording_object_key == f"calls/{user_status}/retained.ogg"
    assert stored.recording_egress_id == f"egress-{user_status}"
    assert stored.summary_text == "Caller request: Opening hours."


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
async def test_delete_call_repeat_owner_returns_provider_free_204(
    async_client,
    client_database_url,
    rs256_clerk_token_for,
) -> None:
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
    stored = await fetch_call(client_database_url, call_id=call_id)

    assert first_response.status_code == 204
    assert repeat_response.status_code == 204
    assert stored.deleted_at is not None
    assert stored.recording_object_key is None


@pytest.mark.anyio
async def test_delete_call_outbox_wake_failure_does_not_change_provider_free_204(
    async_client,
    test_app,
    client_database_url,
    rs256_clerk_token_for,
) -> None:
    class FailingPool:
        def __init__(self) -> None:
            self.jobs: list[tuple[str, dict]] = []

        async def enqueue_job(self, name: str, payload: dict) -> None:
            self.jobs.append((name, payload))
            raise RuntimeError("redis unavailable")

    pool = FailingPool()
    test_app.state.arq_pool = pool
    call_id = await seed_call_with_recording(
        client_database_url,
        clerk_user_id="user_calls",
        email="calls@example.com",
        recording_url="https://stored.example.com/legacy",
        recording_object_key="calls/user_calls/provider-free-delete.ogg",
        recording_egress_id="egress-provider-free-delete",
    )

    response = await async_client.delete(
        f"/api/calls/{call_id}",
        headers={"authorization": f"Bearer {rs256_clerk_token_for('user_calls')}"},
    )
    stored = await fetch_call(client_database_url, call_id=call_id)

    assert response.status_code == 204
    assert pool.jobs == [("outbox_delivery_job", {})]
    assert stored.deleted_at is not None
    assert stored.recording_object_key is None
    assert stored.recording_egress_id is None
