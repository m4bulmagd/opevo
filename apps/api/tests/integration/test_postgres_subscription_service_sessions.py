"""PostgreSQL coverage for services that share one real async session."""

import os
from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.models import Base
from app.models.subscription import Subscription
from app.models.usage_ledger import UsageLedger
from app.models.user import User
from app.services.billing_query_service import BillingQueryService
from app.services.onboarding_service import OnboardingService


@pytest_asyncio.fixture
async def task5_service_session_factory() -> AsyncIterator[
    async_sessionmaker[AsyncSession]
]:
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip(
            "PostgreSQL service-session tests require TEST_DATABASE_URL; "
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

    schema_name = f"task5_services_{uuid4().hex}"
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
async def test_billing_and_onboarding_share_a_real_postgres_async_session(
    task5_service_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with task5_service_session_factory() as session:
        user = User(
            clerk_user_id=f"user_services_{uuid4().hex}",
            email=f"services_{uuid4().hex}@example.com",
        )
        session.add(user)
        await session.flush()
        session.add(
            Subscription(
                user_id=user.id,
                stripe_customer_id=f"cus_{uuid4().hex}",
                stripe_subscription_id=f"sub_{uuid4().hex}",
                plan_tier="starter",
                status="trialing",
                allocated_minutes=60,
            )
        )
        session.add(
            UsageLedger(
                user_id=user.id,
                event_type="subscription_activated",
                minutes_delta=60,
                balance_after=60,
            )
        )
        await session.commit()

        usage = await BillingQueryService(session).get_usage_snapshot(user.id)
        onboarding = await OnboardingService(session).get_status(user.id)

    assert usage.minutes_remaining == 60
    assert usage.subscription_status == "trialing"
    assert onboarding.subscription_status == "trialing"
    assert onboarding.overall_status == "subscription_active"
