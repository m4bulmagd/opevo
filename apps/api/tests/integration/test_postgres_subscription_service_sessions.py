"""PostgreSQL coverage for services that share one real async session."""

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select, text
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
from app.services.billing_service import BillingService
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


@pytest.mark.anyio
async def test_stale_identity_map_cannot_regress_locked_subscription(
    task5_service_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    generation = datetime(2026, 1, 1, tzinfo=UTC)
    initial_watermark = datetime(2026, 2, 1, tzinfo=UTC)
    newer_watermark = datetime(2026, 4, 1, tzinfo=UTC)
    older_event = datetime(2026, 3, 1, tzinfo=UTC)
    newer_period_end = datetime(2026, 6, 1, tzinfo=UTC)

    async with task5_service_session_factory() as seed_session:
        user = User(
            clerk_user_id=f"user_stale_{uuid4().hex}",
            email=f"stale_{uuid4().hex}@example.com",
        )
        seed_session.add(user)
        await seed_session.flush()
        subscription = Subscription(
            user_id=user.id,
            stripe_customer_id="cus_before_concurrent_update",
            stripe_subscription_id="sub_concurrent_ordering",
            plan_tier="starter",
            status="active",
            allocated_minutes=60,
            current_period_end=datetime(2026, 3, 1, tzinfo=UTC),
            stripe_subscription_created_at=generation,
            last_stripe_event_created_at=initial_watermark,
        )
        seed_session.add(subscription)
        await seed_session.commit()
        user_id = user.id

    async with task5_service_session_factory() as stale_session:
        stale_subscription = await stale_session.scalar(
            select(Subscription).where(Subscription.user_id == user_id)
        )
        assert stale_subscription is not None
        assert stale_subscription.status == "active"

        async with task5_service_session_factory() as newer_session:
            current = await newer_session.scalar(
                select(Subscription).where(Subscription.user_id == user_id)
            )
            assert current is not None
            current.status = "past_due"
            current.stripe_customer_id = "cus_after_concurrent_update"
            current.current_period_end = newer_period_end
            current.last_stripe_event_created_at = newer_watermark
            await newer_session.commit()

        service = BillingService(stale_session)
        await service._handle_subscription_event(
            {
                "id": "sub_concurrent_ordering",
                "created": int(generation.timestamp()),
                "status": "active",
                "metadata": {},
                "items": {"data": []},
            },
            "evt_stale_identity_map",
            "customer.subscription.updated",
            older_event,
        )
        await stale_session.commit()

    async with task5_service_session_factory() as verify_session:
        persisted = await verify_session.scalar(
            select(Subscription).where(Subscription.user_id == user_id)
        )

    assert persisted is not None
    assert persisted.status == "past_due"
    assert persisted.stripe_customer_id == "cus_after_concurrent_update"
    assert persisted.current_period_end == newer_period_end
    assert persisted.last_stripe_event_created_at == newer_watermark
