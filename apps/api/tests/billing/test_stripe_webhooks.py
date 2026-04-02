import json
import asyncio
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.notification import Notification
from app.models.phone_number import PhoneNumber
from app.models.subscription import Subscription
from app.models.user import User
from app.models.usage_ledger import UsageLedger
from app.providers.telephony.base import TelephonyProvisioningReviewRequired


@pytest.mark.anyio
async def test_subscription_activation_provisions_usage_ledger(
    async_client,
    client_database_url,
    signed_stripe_headers_factory,
    stripe_subscription_created_payload,
) -> None:
    async def seed_user() -> None:
        engine = create_async_engine(client_database_url, future=True)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session:
            session.add(User(clerk_user_id="user_123", email="billing@example.com"))
            await session.commit()
        await engine.dispose()

    class FakeTelephonyProvider:
        async def provision_number(self, *, country_code: str) -> dict:
            return {
                "e164": "+33123456789",
                "provider_number_id": "pn_123",
                "provider_connection_name": "app-active",
            }

        async def enable_number(self, *, provider_number_id: str) -> str:
            return "app-active"

        async def disable_number(self, *, provider_number_id: str) -> str:
            return "app-disabled"

    async def fetch_numbers() -> list[PhoneNumber]:
        engine = create_async_engine(client_database_url, future=True)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session:
            result = await session.execute(select(PhoneNumber))
            rows = list(result.scalars())
        await engine.dispose()
        return rows

    await seed_user()

    from app.main import app
    from app.webhooks.stripe import get_telephony_provider

    app.dependency_overrides[get_telephony_provider] = lambda: FakeTelephonyProvider()
    response = await async_client.post(
        "/webhooks/stripe",
        content=json.dumps(stripe_subscription_created_payload, separators=(",", ":")).encode("utf-8"),
        headers=signed_stripe_headers_factory(stripe_subscription_created_payload),
    )
    app.dependency_overrides.pop(get_telephony_provider, None)

    assert response.status_code == 202
    assert (await fetch_numbers())[0].e164 == "+33123456789"


@pytest.mark.anyio
async def test_subscription_activation_accepts_stripe_style_signature(
    async_client,
    client_database_url,
    signed_stripe_headers_factory,
    stripe_subscription_created_payload,
) -> None:
    async def seed_user() -> None:
        engine = create_async_engine(client_database_url, future=True)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session:
            session.add(User(clerk_user_id="user_123", email="billing@example.com"))
            await session.commit()
        await engine.dispose()

    class FakeTelephonyProvider:
        async def provision_number(self, *, country_code: str) -> dict:
            return {
                "e164": "+33123456789",
                "provider_number_id": "pn_123",
                "provider_connection_name": "app-active",
            }

        async def enable_number(self, *, provider_number_id: str) -> str:
            return "app-active"

        async def disable_number(self, *, provider_number_id: str) -> str:
            return "app-disabled"

    await seed_user()

    from app.main import app
    from app.webhooks.stripe import get_telephony_provider

    app.dependency_overrides[get_telephony_provider] = lambda: FakeTelephonyProvider()
    response = await async_client.post(
        "/webhooks/stripe",
        content=json.dumps(stripe_subscription_created_payload, separators=(",", ":")).encode("utf-8"),
        headers=signed_stripe_headers_factory(stripe_subscription_created_payload),
    )
    app.dependency_overrides.pop(get_telephony_provider, None)

    assert response.status_code == 202


@pytest.mark.anyio
async def test_subscription_activation_accepts_current_stripe_subscription_shape(
    async_client,
    client_database_url,
    signed_stripe_headers_factory,
    stripe_current_subscription_created_payload,
) -> None:
    async def seed_user() -> None:
        engine = create_async_engine(client_database_url, future=True)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session:
            session.add(User(clerk_user_id="user_123", email="billing@example.com"))
            await session.commit()
        await engine.dispose()

    class FakeTelephonyProvider:
        async def provision_number(self, *, country_code: str) -> dict:
            return {
                "e164": "+33123456789",
                "provider_number_id": "pn_123",
                "provider_connection_name": "app-active",
            }

        async def enable_number(self, *, provider_number_id: str) -> str:
            return "app-active"

        async def disable_number(self, *, provider_number_id: str) -> str:
            return "app-disabled"

    await seed_user()

    from app.main import app
    from app.webhooks.stripe import get_telephony_provider

    app.dependency_overrides[get_telephony_provider] = lambda: FakeTelephonyProvider()
    response = await async_client.post(
        "/webhooks/stripe",
        content=json.dumps(stripe_current_subscription_created_payload, separators=(",", ":")).encode("utf-8"),
        headers=signed_stripe_headers_factory(stripe_current_subscription_created_payload),
    )
    app.dependency_overrides.pop(get_telephony_provider, None)

    assert response.status_code == 202


@pytest.mark.anyio
async def test_subscription_activation_persists_subscription_and_support_notification_when_provisioning_needs_review(
    async_client,
    client_database_url,
    signed_stripe_headers_factory,
    stripe_current_subscription_created_payload,
) -> None:
    async def seed_user() -> None:
        engine = create_async_engine(client_database_url, future=True)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session:
            session.add(User(clerk_user_id="user_123", email="billing@example.com"))
            await session.commit()
        await engine.dispose()

    class ReviewRequiredTelephonyProvider:
        async def provision_number(self, *, country_code: str) -> dict:
            raise TelephonyProvisioningReviewRequired(
                reason="no_affordable_number",
                payload={
                    "event": "phone_number_provisioning_review_required",
                    "country_code": country_code,
                    "contact_support": True,
                },
            )

        async def enable_number(self, *, provider_number_id: str) -> str:
            return "app-active"

        async def disable_number(self, *, provider_number_id: str) -> str:
            return "app-disabled"

    async def fetch_state() -> tuple[list[Subscription], list[Notification], list[UsageLedger], list[PhoneNumber]]:
        engine = create_async_engine(client_database_url, future=True)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session:
            subscriptions = list((await session.execute(select(Subscription))).scalars())
            notifications = list((await session.execute(select(Notification))).scalars())
            ledgers = list((await session.execute(select(UsageLedger))).scalars())
            phone_numbers = list((await session.execute(select(PhoneNumber))).scalars())
        await engine.dispose()
        return subscriptions, notifications, ledgers, phone_numbers

    await seed_user()

    from app.main import app
    from app.webhooks.stripe import get_telephony_provider

    app.dependency_overrides[get_telephony_provider] = lambda: ReviewRequiredTelephonyProvider()
    response = await async_client.post(
        "/webhooks/stripe",
        content=json.dumps(stripe_current_subscription_created_payload, separators=(",", ":")).encode("utf-8"),
        headers=signed_stripe_headers_factory(stripe_current_subscription_created_payload),
    )
    app.dependency_overrides.pop(get_telephony_provider, None)

    subscriptions, notifications, ledgers, phone_numbers = await fetch_state()

    assert response.status_code == 202
    assert subscriptions[0].plan_tier == "starter"
    assert notifications[0].notification_type == "phone_number_provisioning_review_required"
    assert notifications[0].payload["contact_support"] is True
    assert ledgers[0].event_type == "subscription_activated"
    assert not phone_numbers


@pytest.mark.anyio
async def test_invoice_paid_resets_minutes(
    async_client,
    client_database_url,
    signed_stripe_headers_factory,
    stripe_invoice_paid_payload,
) -> None:
    async def seed_subscription() -> None:
        engine = create_async_engine(client_database_url, future=True)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session:
            user = User(clerk_user_id="user_123", email="billing@example.com")
            session.add(user)
            await session.flush()
            session.add(
                Subscription(
                    user_id=user.id,
                    stripe_customer_id="cus_123",
                    stripe_subscription_id="sub_123",
                    plan_tier="standard",
                    status="active",
                    allocated_minutes=120,
                    current_period_start=datetime.fromtimestamp(1710000000, UTC),
                    current_period_end=datetime.fromtimestamp(1712592000, UTC),
                )
            )
            await session.commit()
        await engine.dispose()

    async def fetch_ledgers() -> list[UsageLedger]:
        engine = create_async_engine(client_database_url, future=True)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session:
            result = await session.execute(select(UsageLedger).order_by(UsageLedger.created_at.asc()))
            rows = list(result.scalars())
        await engine.dispose()
        return rows

    await seed_subscription()

    response = await async_client.post(
        "/webhooks/stripe",
        content=json.dumps(stripe_invoice_paid_payload, separators=(",", ":")).encode("utf-8"),
        headers=signed_stripe_headers_factory(stripe_invoice_paid_payload),
    )

    assert response.status_code == 202
    assert (await fetch_ledgers())[-1].event_type == "invoice_paid_reset"
