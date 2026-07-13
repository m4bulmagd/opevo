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
from app.models.outbox_event import OutboxEvent
from app.services.outbox_service import OutboxService


@pytest_asyncio.fixture
async def task5_postgres_session_factory(
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip(
            "PostgreSQL outbox tests require TEST_DATABASE_URL; "
            "the application DATABASE_URL is never used"
        )
    if not database_url.startswith(("postgresql+asyncpg://", "postgresql://")):
        pytest.skip("TEST_DATABASE_URL must identify a PostgreSQL database")
    if database_url.startswith("postgresql://"):
        database_url = database_url.replace(
            "postgresql://",
            "postgresql+asyncpg://",
            1,
        )

    schema_name = f"task5_outbox_{uuid4().hex}"
    quoted_schema = f'"{schema_name}"'
    admin_engine = create_async_engine(database_url, isolation_level="AUTOCOMMIT")
    test_engine = None
    try:
        async with admin_engine.connect() as connection:
            await connection.execute(text(f"CREATE SCHEMA {quoted_schema}"))

        test_engine = create_async_engine(
            database_url,
            connect_args={"server_settings": {"search_path": schema_name}},
        )
        async with test_engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        yield async_sessionmaker(test_engine, expire_on_commit=False)
    finally:
        if test_engine is not None:
            await test_engine.dispose()
        async with admin_engine.connect() as connection:
            await connection.execute(
                text(f"DROP SCHEMA IF EXISTS {quoted_schema} CASCADE")
            )
        await admin_engine.dispose()


@pytest.mark.anyio
async def test_postgres_outbox_rolls_back_with_its_business_transaction(
    task5_postgres_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with task5_postgres_session_factory() as session:
        await OutboxService(session).add(
            topic="phone.disable",
            aggregate_type="subscription",
            aggregate_id=uuid4(),
            idempotency_key="stripe:customer.subscription.updated:evt_pg_rollback",
            payload={"user_id": str(uuid4())},
        )
        await session.rollback()

        assert await session.scalar(
            select(func.count()).select_from(OutboxEvent)
        ) == 0


@pytest.mark.anyio
async def test_postgres_outbox_rejects_duplicate_intent_identity(
    task5_postgres_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    arguments = {
        "topic": "phone.disable",
        "aggregate_type": "subscription",
        "aggregate_id": uuid4(),
        "idempotency_key": "stripe:customer.subscription.updated:evt_pg_duplicate",
        "payload": {"user_id": str(uuid4())},
    }
    async with task5_postgres_session_factory() as session:
        await OutboxService(session).add(**arguments)
        await session.commit()

        with pytest.raises(IntegrityError):
            await OutboxService(session).add(**arguments)
        await session.rollback()

        assert await session.scalar(
            select(func.count()).select_from(OutboxEvent)
        ) == 1
