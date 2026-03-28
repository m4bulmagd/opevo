from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.call import Call
from app.models.call_message import CallMessage
from app.models.user import User


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
    async_client, client_database_url, rs256_clerk_token_for
) -> None:
    class FakeRecordingService:
        async def get_access_url(self, *, call_id, user_id, stored_url):
            assert stored_url == "https://stored.example.com/old"
            return "https://signed.example.com/fresh"

    call_id = await seed_call_with_recording(
        client_database_url,
        clerk_user_id="user_calls",
        email="calls@example.com",
        recording_url="https://stored.example.com/old",
    )

    from app.main import app
    from app.routers.calls import get_call_history_service
    from app.services.call_history_service import CallHistoryService

    async def override_service():
        engine = create_async_engine(client_database_url, future=True)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session:
            yield CallHistoryService(session, recording_service=FakeRecordingService())
        await engine.dispose()

    app.dependency_overrides[get_call_history_service] = override_service
    try:
        response = await async_client.get(
            f"/api/calls/{call_id}",
            headers={"authorization": f"Bearer {rs256_clerk_token_for('user_calls')}"},
        )
    finally:
        app.dependency_overrides.pop(get_call_history_service, None)

    assert response.status_code == 200
    assert response.json()["recording_url"] == "https://signed.example.com/fresh"


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
