from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database import get_session
from app.models import Base
from app.models.activation_event import ActivationEvent
from app.models.business_profile import BusinessProfile
from app.models.call import Call
from app.models.call_message import CallMessage
from app.models.customer_activation import CustomerActivation
from app.models.notification import Notification
from app.models.outbox_event import OutboxEvent
from app.models.phone_number import PhoneNumber
from app.models.phone_number_provisioning import PhoneNumberProvisioning
from app.models.subscription import Subscription
from app.models.usage_ledger import UsageLedger
from app.models.user import User
from app.schemas.business_profile import WEEKDAYS
from app.services.forwarding_verification_service import as_utc


FIXED_NOW = datetime(2026, 7, 18, 10, 0, tzinfo=UTC)


@asynccontextmanager
async def _api_client(
    tmp_path: Path,
    settings,
    *,
    app_env: str,
    billing_mode: str,
    telephony_mode: str = "fake",
    real_subscription: bool = False,
    verification_ready: bool = False,
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
        owner.country_code = "FR"
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
        if verification_ready:
            profile = BusinessProfile(
                user_id=owner.id,
                owner_name="Camille Martin",
                business_name="Atelier Martin",
                business_type="Plomberie",
                public_description="Dépannage et installation de plomberie.",
                timezone="Europe/Paris",
                business_hours={
                    day: {"closed": True, "intervals": []}
                    for day in WEEKDAYS
                },
                existing_phone_e164="+33199000000",
                confirmed_carrier="orange",
                receptionist_name="Léa",
                content_revision=1,
                routing_revision=1,
            )
            activation = CustomerActivation(
                user_id=owner.id,
                profile_confirmed_revision=1,
                profile_confirmed_at=FIXED_NOW - timedelta(hours=1),
                provisioning_consented_at=FIXED_NOW - timedelta(minutes=30),
            )
            phone = PhoneNumber(
                user_id=owner.id,
                e164="+33999000000",
                country_code="FR",
                provider="fake",
                provider_number_id="fake_development_verification",
                provider_connection_name="fake-connection",
                is_active=False,
            )
            session.add_all([profile, activation, phone])
            await session.flush()
            session.add_all(
                [
                    PhoneNumberProvisioning(
                        user_id=owner.id,
                        phone_number_id=phone.id,
                        target_country_code="FR",
                        status="succeeded",
                        attempt_count=1,
                        can_retry=False,
                        provider_operation_key=(
                            f"activation:phone.provision:{activation.id}"
                        ),
                    ),
                    Subscription(
                        user_id=owner.id,
                        stripe_customer_id="local_verification_customer",
                        stripe_subscription_id="local_verification_subscription",
                        plan_tier="starter",
                        status="active",
                        allocated_minutes=60,
                        current_period_start=FIXED_NOW - timedelta(days=1),
                        current_period_end=FIXED_NOW + timedelta(days=30),
                    ),
                    UsageLedger(
                        user_id=owner.id,
                        event_type="subscription_activated",
                        source_id="local_verification_invoice",
                        minutes_delta=60,
                        balance_after=60,
                    ),
                ]
            )
        await session.commit()

    configured = settings.model_copy(
        update={
            "app_env": app_env,
            "billing_mode": billing_mode,
            "telephony_mode": telephony_mode,
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
async def test_development_forwarding_simulator_requires_authentication(
    tmp_path: Path,
    settings,
) -> None:
    async with _api_client(
        tmp_path,
        settings,
        app_env="development",
        billing_mode="fake",
        telephony_mode="fake",
    ) as (client, _session_factory, _users):
        response = await client.post(
            "/api/development/simulate-forwarded-call"
        )

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
async def test_development_forwarding_simulator_requires_fake_telephony_mode(
    tmp_path: Path,
    settings,
    rs256_clerk_token_for,
) -> None:
    async with _api_client(
        tmp_path,
        settings,
        app_env="development",
        billing_mode="fake",
        telephony_mode="telnyx",
    ) as (client, _session_factory, _users):
        response = await client.post(
            "/api/development/simulate-forwarded-call",
            headers={
                "Authorization": (
                    f"Bearer {rs256_clerk_token_for('development_owner')}"
                )
            },
        )

    assert response.status_code == 409
    assert response.json() == {"detail": {"code": "local_telephony_disabled"}}


@pytest.mark.anyio
async def test_development_simulator_completes_real_state_machine_without_call_artifacts(
    tmp_path: Path,
    settings,
    rs256_clerk_token_for,
) -> None:
    async with _api_client(
        tmp_path,
        settings,
        app_env="development",
        billing_mode="fake",
        telephony_mode="fake",
        verification_ready=True,
    ) as (client, session_factory, users):
        headers = {
            "Authorization": (
                f"Bearer {rs256_clerk_token_for('development_owner')}"
            )
        }
        opened = await client.post(
            "/api/activation/open-verification-window",
            headers=headers,
        )
        simulated = await client.post(
            "/api/development/simulate-forwarded-call",
            headers=headers,
        )

        async with session_factory() as session:
            call_count = await session.scalar(
                select(func.count()).select_from(Call)
            )
            message_count = await session.scalar(
                select(func.count()).select_from(CallMessage)
            )
            notification_count = await session.scalar(
                select(func.count()).select_from(Notification)
            )
            usage_event_count = await session.scalar(
                select(func.count()).select_from(UsageLedger)
            )
            outbox_count = await session.scalar(
                select(func.count()).select_from(OutboxEvent)
            )
            activation = await session.scalar(
                select(CustomerActivation).where(
                    CustomerActivation.user_id == users["owner"].id
                )
            )
            claimed_event = await session.scalar(
                select(ActivationEvent).where(
                    ActivationEvent.event_type == "verification_window_claimed"
                )
            )

    assert opened.status_code == 200
    opened_snapshot = opened.json()
    assert opened_snapshot["stage"] == "verification_window_open"
    assert opened_snapshot["activation"]["verification_status"] == "open"
    assert opened_snapshot["activation"]["verification_window_started_at"] is not None
    assert opened_snapshot["activation"]["verification_window_expires_at"] is not None
    assert "verification_session_id" not in opened.text
    assert "routing_fingerprint" not in opened.text
    assert "room_name" not in opened.text

    assert simulated.status_code == 200
    simulated_snapshot = simulated.json()
    assert simulated_snapshot["stage"] == "ready_to_activate"
    assert simulated_snapshot["activation"]["verification_status"] == "succeeded"
    assert simulated_snapshot["activation"]["forwarding_verified_at"] is not None
    assert call_count == 0
    assert message_count == 0
    assert notification_count == 0
    assert usage_event_count == 1
    assert outbox_count == 0
    assert activation is not None
    assert activation.go_live_requested_at is None
    assert activation.go_live_approved_at is None
    assert activation.activated_at is None
    assert claimed_event is not None
    assert activation.verification_window_started_at is not None
    expected_window_epoch = int(
        as_utc(activation.verification_window_started_at).timestamp()
    )
    assert claimed_event.event_metadata["room_name"] == (
        f"local-verification-{activation.id}-{expected_window_epoch}"
    )


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
@pytest.mark.parametrize(
    "path",
    [
        "/api/development/activate-starter",
        "/api/development/simulate-forwarded-call",
    ],
)
async def test_development_router_is_absent_outside_development(
    settings,
    app_env: str,
    path: str,
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
        response = await client.post(path)

    assert response.status_code == 404
