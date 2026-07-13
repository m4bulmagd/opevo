from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.models.call import Call
from app.models.call_message import CallMessage
from app.models.user import User
from app.schemas.agent_runtime import TranscriptAppendRequest
from app.services.transcript_service import (
    TranscriptSequenceConflictError,
    TranscriptService,
)


@pytest_asyncio.fixture
async def postgres_session_factory():
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("PostgreSQL transcript concurrency tests require TEST_DATABASE_URL")
    if database_url.startswith("postgresql://"):
        database_url = database_url.replace(
            "postgresql://",
            "postgresql+asyncpg://",
            1,
        )
    if not database_url.startswith("postgresql+asyncpg://"):
        pytest.skip("TEST_DATABASE_URL must identify a PostgreSQL database")

    engine = create_async_engine(database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


async def _create_call(factory: async_sessionmaker[AsyncSession]) -> Call:
    suffix = uuid4().hex
    async with factory() as session:
        user = User(
            clerk_user_id=f"transcript_concurrency_{suffix}",
            email=f"transcript-concurrency-{suffix}@example.com",
        )
        session.add(user)
        await session.flush()
        call = Call(user_id=user.id, status="connected")
        session.add(call)
        await session.commit()
        return call


async def _append_once(
    factory: async_sessionmaker[AsyncSession],
    *,
    call_id,
    item: TranscriptAppendRequest,
    start: asyncio.Event,
) -> str:
    async with factory() as session:
        await start.wait()
        try:
            result = await TranscriptService(session).append(
                call_id=call_id,
                item=item,
            )
            await session.commit()
            return result.status
        except TranscriptSequenceConflictError:
            await session.rollback()
            return "sequence_conflict"


@pytest.mark.anyio
async def test_concurrent_exact_append_stores_once_and_returns_duplicate(
    postgres_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    call = await _create_call(postgres_session_factory)
    start = asyncio.Event()
    item = TranscriptAppendRequest(
        sequence_number=1,
        speaker="CALLER",
        text="Exactly once",
    )
    tasks = [
        asyncio.create_task(
            _append_once(
                postgres_session_factory,
                call_id=call.id,
                item=item,
                start=start,
            )
        )
        for _ in range(2)
    ]
    start.set()

    results = await asyncio.gather(*tasks)

    assert sorted(results) == ["duplicate", "stored"]
    async with postgres_session_factory() as session:
        rows = list(
            (
                await session.execute(
                    select(CallMessage).where(CallMessage.call_id == call.id)
                )
            ).scalars()
        )
    assert [(row.sequence_number, row.speaker, row.text) for row in rows] == [
        (1, "CALLER", "Exactly once")
    ]


@pytest.mark.anyio
async def test_concurrent_different_content_keeps_first_write_and_conflicts_other(
    postgres_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    call = await _create_call(postgres_session_factory)
    start = asyncio.Event()
    items = [
        TranscriptAppendRequest(sequence_number=1, speaker="CALLER", text="First"),
        TranscriptAppendRequest(sequence_number=1, speaker="AGENT", text="Second"),
    ]
    tasks = [
        asyncio.create_task(
            _append_once(
                postgres_session_factory,
                call_id=call.id,
                item=item,
                start=start,
            )
        )
        for item in items
    ]
    start.set()

    results = await asyncio.gather(*tasks)

    assert sorted(results) == ["sequence_conflict", "stored"]
    async with postgres_session_factory() as session:
        row = await session.scalar(
            select(CallMessage).where(
                CallMessage.call_id == call.id,
                CallMessage.sequence_number == 1,
            )
        )
    assert row is not None
    assert (row.speaker, row.text) in {("CALLER", "First"), ("AGENT", "Second")}
