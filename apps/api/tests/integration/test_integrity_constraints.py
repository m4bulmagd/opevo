import asyncio
import os
from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.models import Base
from app.models.call import Call
from app.models.call_message import CallMessage
from app.models.subscription import Subscription
from app.models.usage_ledger import UsageLedger
from app.models.user import User
from app.models.webhook_event import WebhookEvent
from app.repositories.webhook_event_repository import WebhookEventRepository


@pytest_asyncio.fixture
async def postgres_session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip(
            "PostgreSQL integrity tests require TEST_DATABASE_URL; "
            "the normal application DATABASE_URL is never used"
        )
    if not database_url.startswith(("postgresql+asyncpg://", "postgresql://")):
        pytest.skip("TEST_DATABASE_URL must identify a PostgreSQL database")
    if database_url.startswith("postgresql://"):
        database_url = database_url.replace(
            "postgresql://",
            "postgresql+asyncpg://",
            1,
        )

    schema_name = f"task4_integrity_{uuid4().hex}"
    quoted_schema = f'"{schema_name}"'
    admin_engine = create_async_engine(database_url, isolation_level="AUTOCOMMIT")
    test_engine = None

    try:
        async with admin_engine.connect() as connection:
            await connection.execute(text(f"CREATE SCHEMA {quoted_schema}"))

        test_engine = create_async_engine(
            database_url,
            connect_args={"server_settings": {"search_path": quoted_schema}},
        )
        async with test_engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        yield async_sessionmaker(test_engine, expire_on_commit=False)
    finally:
        if test_engine is not None:
            await test_engine.dispose()
        async with admin_engine.connect() as connection:
            await connection.execute(text(f"DROP SCHEMA IF EXISTS {quoted_schema} CASCADE"))
        await admin_engine.dispose()


async def _create_user(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    suffix: str,
) -> User:
    async with session_factory() as session:
        user = User(
            clerk_user_id=f"user_{suffix}_{uuid4().hex}",
            email=f"{suffix}_{uuid4().hex}@example.com",
        )
        session.add(user)
        await session.commit()
        return user


async def _commit_one(
    session_factory: async_sessionmaker[AsyncSession],
    instance: object,
) -> bool:
    async with session_factory() as session:
        session.add(instance)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            return False
        return True


@pytest.mark.anyio
async def test_webhook_repository_race_has_one_durable_provider_event(
    postgres_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    external_event_id = f"evt_{uuid4().hex}"

    async def record() -> bool:
        async with postgres_session_factory() as session:
            inserted = await WebhookEventRepository(session).record_if_new(
                provider="stripe",
                external_event_id=external_event_id,
                event_type="invoice.paid",
                payload={"id": external_event_id},
            )
            await session.commit()
            return inserted

    results = await asyncio.gather(record(), record())

    assert sorted(results) == [False, True]
    async with postgres_session_factory() as session:
        durable_count = await session.scalar(
            select(func.count())
            .select_from(WebhookEvent)
            .where(
                WebhookEvent.provider == "stripe",
                WebhookEvent.external_event_id == external_event_id,
            )
        )
    assert durable_count == 1


@pytest.mark.anyio
async def test_call_debit_race_has_one_durable_call_event(
    postgres_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    user = await _create_user(postgres_session_factory, suffix="call_debit")
    async with postgres_session_factory() as session:
        call = Call(user_id=user.id, status="completed")
        session.add(call)
        await session.commit()

    results = await asyncio.gather(
        _commit_one(
            postgres_session_factory,
            UsageLedger(
                user_id=user.id,
                call_id=call.id,
                event_type="call_completed",
                minutes_delta=-1,
            ),
        ),
        _commit_one(
            postgres_session_factory,
            UsageLedger(
                user_id=user.id,
                call_id=call.id,
                event_type="call_completed",
                minutes_delta=-1,
            ),
        ),
    )

    assert sorted(results) == [False, True]
    async with postgres_session_factory() as session:
        durable_count = await session.scalar(
            select(func.count())
            .select_from(UsageLedger)
            .where(
                UsageLedger.call_id == call.id,
                UsageLedger.event_type == "call_completed",
            )
        )
    assert durable_count == 1


@pytest.mark.anyio
async def test_integrity_constraints_cover_all_identities(
    postgres_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    user = await _create_user(postgres_session_factory, suffix="identities")
    async with postgres_session_factory() as session:
        call = Call(user_id=user.id, status="completed")
        session.add(call)
        await session.commit()

    cases = [
        (
            WebhookEvent(
                provider="stripe",
                external_event_id="evt_identity",
                event_type="invoice.paid",
                payload={},
            ),
            WebhookEvent(
                provider="stripe",
                external_event_id="evt_identity",
                event_type="invoice.updated",
                payload={},
            ),
        ),
        (
            UsageLedger(
                user_id=user.id,
                event_type="invoice_paid_reset",
                source_id="in_identity",
                minutes_delta=60,
            ),
            UsageLedger(
                user_id=user.id,
                event_type="invoice_paid_reset",
                source_id="in_identity",
                minutes_delta=60,
            ),
        ),
        (
            CallMessage(call_id=call.id, speaker="CALLER", text="one", sequence_number=1),
            CallMessage(call_id=call.id, speaker="AGENT", text="two", sequence_number=1),
        ),
        (
            Subscription(
                user_id=user.id,
                plan_tier="starter",
                status="active",
                allocated_minutes=60,
            ),
            Subscription(
                user_id=user.id,
                plan_tier="starter",
                status="active",
                allocated_minutes=60,
            ),
        ),
    ]

    for first, duplicate in cases:
        assert await _commit_one(postgres_session_factory, first) is True
        assert await _commit_one(postgres_session_factory, duplicate) is False

    assert await _commit_one(
        postgres_session_factory,
        WebhookEvent(
            provider="clerk",
            external_event_id="evt_identity",
            event_type="user.created",
            payload={},
        ),
    ) is True
    assert await _commit_one(
        postgres_session_factory,
        UsageLedger(
            user_id=user.id,
            event_type="adjustment",
            source_id=None,
            minutes_delta=1,
        ),
    ) is True


@pytest.mark.anyio
async def test_one_active_call_per_user_but_completed_call_releases_identity(
    postgres_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    user = await _create_user(postgres_session_factory, suffix="active_call")

    assert await _commit_one(
        postgres_session_factory,
        Call(user_id=user.id, status="pending"),
    ) is True
    assert await _commit_one(
        postgres_session_factory,
        Call(user_id=user.id, status="finalizing"),
    ) is False

    async with postgres_session_factory() as session:
        active_call = await session.scalar(
            select(Call).where(Call.user_id == user.id, Call.status == "pending")
        )
        assert active_call is not None
        active_call.status = "completed"
        await session.commit()

    assert await _commit_one(
        postgres_session_factory,
        Call(user_id=user.id, status="connected"),
    ) is True


@pytest.mark.anyio
@pytest.mark.parametrize(
    "instance_factory",
    [
        lambda user_id: Subscription(
            user_id=user_id,
            plan_tier="starter",
            status="active",
            allocated_minutes=-1,
        ),
        lambda user_id: Call(user_id=user_id, status="completed", duration_seconds=-1),
        lambda user_id: Call(user_id=user_id, status="completed", minutes_charged=-1),
    ],
)
async def test_nonnegative_checks_reject_negative_values(
    postgres_session_factory: async_sessionmaker[AsyncSession],
    instance_factory,
) -> None:
    user = await _create_user(postgres_session_factory, suffix="nonnegative")

    assert await _commit_one(postgres_session_factory, instance_factory(user.id)) is False
