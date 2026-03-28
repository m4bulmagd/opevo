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
