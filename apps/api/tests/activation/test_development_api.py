from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database import get_session
from app.models import Base
from app.models.subscription import Subscription
from app.models.user import User


FIXED_NOW = datetime(2026, 7, 18, 10, 0, tzinfo=UTC)


@asynccontextmanager
async def _api_client(
    tmp_path: Path,
    settings,
    *,
    app_env: str,
    billing_mode: str,
    real_subscription: bool = False,
) -> AsyncIterator[tuple[httpx.AsyncClient, async_sessionmaker, dict[str, User]]]:
    from app import main as main_module

    database_url = f"sqlite+aiosqlite:///{tmp_path / 'development-api.db'}"
    engine = create_async_engine(database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    users: dict[str, User] = {}
    async with session_factory() as session:
        owner = User(
            clerk_user_id="development_owner",
            email="development-owner@example.com",
        )
        other = User(
            clerk_user_id="development_other",
            email="development-other@example.com",
        )
        session.add_all([owner, other])
        await session.flush()
        users = {"owner": owner, "other": other}
        if real_subscription:
            session.add(
                Subscription(
                    user_id=owner.id,
                    stripe_customer_id="cus_real_development_owner",
                    stripe_subscription_id="sub_real_development_owner",
                    plan_tier="starter",
                    status="active",
                    allocated_minutes=60,
                    current_period_start=FIXED_NOW,
                    current_period_end=FIXED_NOW + timedelta(days=30),
                    stripe_subscription_created_at=FIXED_NOW,
                    last_stripe_event_created_at=FIXED_NOW,
                )
            )
        await session.commit()

    configured = settings.model_copy(
        update={
            "app_env": app_env,
            "billing_mode": billing_mode,
            "database_url": database_url,
        }
    )
    application = main_module.create_app(configured)

    async def override_get_session():
        async with session_factory() as session:
            yield session

    application.dependency_overrides[get_session] = override_get_session
    transport = httpx.ASGITransport(app=application)
    try:
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            yield client, session_factory, users
    finally:
        application.dependency_overrides.clear()
        await engine.dispose()


@pytest.mark.anyio
async def test_development_starter_requires_authentication(
    tmp_path: Path,
    settings,
) -> None:
    async with _api_client(
        tmp_path,
        settings,
        app_env="development",
        billing_mode="fake",
    ) as (client, _session_factory, _users):
        response = await client.post("/api/development/activate-starter")

    assert response.status_code == 401


@pytest.mark.anyio
async def test_development_starter_requires_fake_billing_mode(
    tmp_path: Path,
    settings,
    rs256_clerk_token_for,
) -> None:
    async with _api_client(
        tmp_path,
        settings,
        app_env="development",
        billing_mode="stripe",
    ) as (client, _session_factory, _users):
        response = await client.post(
            "/api/development/activate-starter",
            headers={
                "Authorization": (
                    f"Bearer {rs256_clerk_token_for('development_owner')}"
                )
            },
        )

    assert response.status_code == 409
    assert response.json() == {"detail": {"code": "local_billing_disabled"}}


@pytest.mark.anyio
async def test_development_starter_returns_canonical_snapshot_for_owner_only(
    tmp_path: Path,
    settings,
    rs256_clerk_token_for,
) -> None:
    async with _api_client(
        tmp_path,
        settings,
        app_env="development",
        billing_mode="fake",
    ) as (client, session_factory, users):
        response = await client.post(
            "/api/development/activate-starter",
            headers={
                "Authorization": (
                    f"Bearer {rs256_clerk_token_for('development_owner')}"
                )
            },
        )

        assert response.status_code == 200
        snapshot = response.json()
        assert snapshot["billing"]["eligible"] is True
        assert snapshot["billing"]["plan_tier"] == "starter"
        assert snapshot["billing"]["subscription_status"] == "active"
        assert snapshot["billing"]["allocated_minutes"] == 60
        assert snapshot["billing"]["minutes_remaining"] == 60
        assert snapshot["stage"] == "profile_required"

        async with session_factory() as session:
            subscriptions = list(
                (
                    await session.scalars(
                        select(Subscription).order_by(Subscription.user_id)
                    )
                ).all()
            )

    assert len(subscriptions) == 1
    assert subscriptions[0].user_id == users["owner"].id
    assert subscriptions[0].user_id != users["other"].id
    assert subscriptions[0].stripe_subscription_id == (
        f"local_subscription_{users['owner'].id}"
    )


@pytest.mark.anyio
async def test_development_starter_reports_real_subscription_conflict_safely(
    tmp_path: Path,
    settings,
    rs256_clerk_token_for,
) -> None:
    async with _api_client(
        tmp_path,
        settings,
        app_env="development",
        billing_mode="fake",
        real_subscription=True,
    ) as (client, _session_factory, _users):
        response = await client.post(
            "/api/development/activate-starter",
            headers={
                "Authorization": (
                    f"Bearer {rs256_clerk_token_for('development_owner')}"
                )
            },
        )

    assert response.status_code == 409
    assert response.json() == {"detail": {"code": "real_subscription_present"}}


@pytest.mark.anyio
@pytest.mark.parametrize("app_env", ["test", "staging"])
async def test_development_starter_router_is_absent_outside_development(
    settings,
    app_env: str,
) -> None:
    from app import main as main_module

    application = main_module.create_app(
        settings.model_copy(
            update={
                "app_env": app_env,
                "billing_mode": "fake",
            }
        )
    )
    transport = httpx.ASGITransport(app=application)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        response = await client.post("/api/development/activate-starter")

    assert response.status_code == 404
