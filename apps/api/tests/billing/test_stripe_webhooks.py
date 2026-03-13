import json
import asyncio

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.phone_number import PhoneNumber
from app.models.user import User


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
async def test_invoice_paid_resets_minutes(
    async_client,
    signed_stripe_headers_factory,
    stripe_invoice_paid_payload,
) -> None:
    response = await async_client.post(
        "/webhooks/stripe",
        content=json.dumps(stripe_invoice_paid_payload, separators=(",", ":")).encode("utf-8"),
        headers=signed_stripe_headers_factory(stripe_invoice_paid_payload),
    )

    assert response.status_code == 202
