from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.agent_config import AgentConfig
from app.models.call import Call
from app.models.call_message import CallMessage
from app.models.phone_number import PhoneNumber
from app.models.user import User
from app.services.call_history_service import CallHistoryService


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
        status="completed",
        started_at=datetime(2026, 3, 28, 10, 0, tzinfo=UTC),
        ended_at=datetime(2026, 3, 28, 10, 1, tzinfo=UTC),
        duration_seconds=60,
        minutes_charged=1,
        summary_text="Caller request: Opening hours.",
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
        "user_calls",
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
        "user_calls",
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
