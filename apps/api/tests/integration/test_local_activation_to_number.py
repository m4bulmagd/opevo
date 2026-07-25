from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from fastapi import Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import Settings
from app.core.database import get_session
from app.models import Base
from app.models.customer_activation import CustomerActivation
from app.models.outbox_event import OutboxEvent
from app.models.phone_number import PhoneNumber
from app.models.subscription import Subscription
from app.models.user import User
from app.models.usage_ledger import UsageLedger
from app.providers.subscriptions.fake import FakeSubscriptionProvider
from app.providers.carrier_lookup.fake import FakeCarrierLookupProvider
from app.providers.telephony.fake import FakeTelephonyProvider
from app.routers.activation import get_carrier_lookup_service
from app.services.carrier_lookup_service import CarrierLookupService
from app.workers.jobs.outbox_delivery import outbox_delivery_job


LOCAL_TOKEN = "presvo-local-development-token"
# ARCEP reserves the 01 99 00 range for audiovisual fiction and does not
# assign it to subscribers (national numbering plan, version 2026-01-01).
ARCEP_FICTIONAL_FIXED_NUMBER = "+33 1 99 00 00 00"


def _complete_profile_payload() -> dict[str, object]:
    business_hours = {
        day: {
            "closed": day in {"saturday", "sunday"},
            "intervals": (
                []
                if day in {"saturday", "sunday"}
                else [{"start": "09:00", "end": "18:00"}]
            ),
        }
        for day in (
            "monday",
            "tuesday",
            "wednesday",
            "thursday",
            "friday",
            "saturday",
            "sunday",
        )
    }
    return {
        "owner_name": "Camille Martin",
        "business_name": "Atelier Martin",
        "business_type": "Plomberie",
        "public_description": "Dépannage et installation de plomberie.",
        "timezone": "Europe/Paris",
        "business_hours": business_hours,
        "existing_phone_e164": ARCEP_FICTIONAL_FIXED_NUMBER,
        "confirmed_carrier": "orange",
        "receptionist_name": "Léa",
        "faqs": [
            {
                "question": "Intervenez-vous le week-end ?",
                "answer": "Oui, uniquement pour les urgences.",
            }
        ],
        "special_instructions": "Toujours demander le code postal.",
        "escalation_notes": "Transférer les urgences au propriétaire.",
    }


@pytest_asyncio.fixture
async def local_client(
    tmp_path: Path,
) -> AsyncIterator[tuple[httpx.AsyncClient, async_sessionmaker[AsyncSession]]]:
    from app.main import create_app

    database_url = f"sqlite+aiosqlite:///{tmp_path / 'local-activation.db'}"
    engine = create_async_engine(database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    settings = Settings(
        app_env="development",
        database_url=database_url,
        redis_url="redis://localhost:6379/0",
        activation_flow_enabled=True,
        auth_mode="local",
        local_auth_token=LOCAL_TOKEN,
        billing_mode="fake",
        carrier_lookup_mode="fake",
        telephony_mode="fake",
    )
    application = create_app(settings)

    async def override_get_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    def override_carrier_lookup_service(
        session: AsyncSession = Depends(get_session),
    ) -> CarrierLookupService:
        return CarrierLookupService(
            session,
            provider=FakeCarrierLookupProvider(),
        )

    application.dependency_overrides[get_session] = override_get_session
    application.dependency_overrides[get_carrier_lookup_service] = (
        override_carrier_lookup_service
    )
    transport = httpx.ASGITransport(app=application)
    try:
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            yield client, session_factory
    finally:
        application.dependency_overrides.clear()
        await engine.dispose()


@pytest.mark.anyio
async def test_provider_free_journey_reaches_forwarding_required(
    local_client: tuple[httpx.AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, session_factory = local_client
    headers = {"Authorization": f"Bearer {LOCAL_TOKEN}"}

    saved = await client.put(
        "/api/business-profile",
        headers=headers,
        json=_complete_profile_payload(),
    )
    assert saved.status_code == 200

    carrier = await client.post(
        "/api/activation/lookup-carrier",
        headers=headers,
    )
    assert carrier.status_code == 200
    assert carrier.json()["normalized_carrier"] == "orange"

    confirmed = await client.post(
        "/api/activation/confirm-profile",
        headers=headers,
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["stage"] == "payment_required"

    paid = await client.post(
        "/api/development/activate-starter",
        headers=headers,
    )
    assert paid.status_code == 200
    assert paid.json()["stage"] == "provisioning_consent_required"

    async with session_factory() as session:
        local_user = await session.scalar(
            select(User).where(User.clerk_user_id == "local_presvo_user")
        )
        pre_consent_events = await session.scalar(
            select(func.count(OutboxEvent.id)).where(
                OutboxEvent.topic == "phone.provision"
            )
        )
        assert local_user is not None
        assert pre_consent_events == 0

    accepted = await client.post(
        "/api/activation/confirm-provisioning",
        headers=headers,
    )
    assert accepted.status_code == 202
    assert accepted.json()["stage"] == "provisioning"

    async with session_factory() as session:
        queued = await session.scalar(
            select(OutboxEvent).where(OutboxEvent.topic == "phone.provision")
        )
        activation = await session.scalar(select(CustomerActivation))
        subscription = await session.scalar(select(Subscription))
        grant = await session.scalar(select(UsageLedger))
        assert queued is not None
        assert queued.status == "pending"
        assert queued.aggregate_id == local_user.id
        assert activation is not None
        assert activation.provisioning_idempotency_key == (
            f"activation:provision:{activation.id}:g1"
        )
        assert subscription is not None
        assert subscription.stripe_subscription_id == (
            f"local_subscription_{local_user.id}_g1"
        )
        assert subscription.lifecycle_generation == 1
        assert grant is not None
        assert grant.source_id == f"local_invoice_{local_user.id}_g1"

    delivery = await outbox_delivery_job(
        {
            "session_factory": session_factory,
            "telephony_provider": FakeTelephonyProvider(),
        }
    )
    assert delivery == {
        "claimed": 1,
        "delivered": 1,
        "retried": 0,
        "failed": 0,
    }

    async with session_factory() as session:
        delivered = await session.get(OutboxEvent, queued.id)
        assert delivered is not None
        assert delivered.status == "delivered"
        assert delivered.delivered_at is not None

    response = await client.get("/api/activation", headers=headers)
    assert response.status_code == 200
    snapshot = response.json()
    assert snapshot["stage"] == "forwarding_required"
    assert snapshot["number"]["assigned_e164"].startswith("+339")
    assert snapshot["number"]["country_code"] == "FR"
    assert snapshot["number"]["provider_ready"] is True
    assert snapshot["number"]["provisioning_status"] == "succeeded"

    first_number = snapshot["number"]["assigned_e164"]
    deactivation = await client.post(
        "/api/account/deactivate",
        headers=headers,
        json={"confirmation": "DEACTIVATE"},
    )
    assert deactivation.status_code == 202
    assert deactivation.json()["status"] == "deactivating"

    cleanup = await outbox_delivery_job(
        {
            "session_factory": session_factory,
            "subscription_provider": FakeSubscriptionProvider(),
            "telephony_provider": FakeTelephonyProvider(),
        }
    )
    assert cleanup == {
        "claimed": 1,
        "delivered": 1,
        "retried": 0,
        "failed": 0,
    }
    account = await client.get("/api/account", headers=headers)
    assert account.status_code == 200
    assert account.json()["status"] == "inactive"

    reactivated = await client.post(
        "/api/development/activate-starter",
        headers=headers,
    )
    assert reactivated.status_code == 200
    assert reactivated.json()["stage"] == "profile_required"
    refreshed_carrier = await client.post(
        "/api/activation/lookup-carrier",
        headers=headers,
    )
    assert refreshed_carrier.status_code == 200
    refreshed_profile = await client.put(
        "/api/business-profile",
        headers=headers,
        json=_complete_profile_payload(),
    )
    assert refreshed_profile.status_code == 200
    reconfirmed = await client.post(
        "/api/activation/confirm-profile",
        headers=headers,
    )
    assert reconfirmed.status_code == 200, reconfirmed.text
    assert reconfirmed.json()["stage"] == "provisioning_consent_required"

    async with session_factory() as session:
        current_user = await session.scalar(select(User))
        subscription = await session.scalar(select(Subscription))
        grants = list(
            (
                await session.execute(
                    select(UsageLedger).order_by(UsageLedger.created_at)
                )
            ).scalars()
        )
        phone = await session.scalar(select(PhoneNumber))
        assert current_user is not None
        assert current_user.status == "active"
        assert current_user.lifecycle_generation == 2
        assert subscription is not None
        assert subscription.stripe_subscription_id == (
            f"local_subscription_{current_user.id}_g2"
        )
        assert subscription.lifecycle_generation == 2
        assert [grant.source_id for grant in grants] == [
            f"local_invoice_{current_user.id}_g1",
            f"local_invoice_{current_user.id}_g2",
        ]
        assert phone is None

    second_consent = await client.post(
        "/api/activation/confirm-provisioning",
        headers=headers,
    )
    assert second_consent.status_code == 202

    async with session_factory() as session:
        activation = await session.scalar(select(CustomerActivation))
        second_queued = await session.scalar(
            select(OutboxEvent).where(
                OutboxEvent.topic == "phone.provision",
                OutboxEvent.status == "pending",
            )
        )
        assert activation is not None
        assert activation.provisioning_idempotency_key == (
            f"activation:provision:{activation.id}:g2"
        )
        assert second_queued is not None
        assert second_queued.idempotency_key == activation.provisioning_idempotency_key

    second_delivery = await outbox_delivery_job(
        {
            "session_factory": session_factory,
            "telephony_provider": FakeTelephonyProvider(),
        }
    )
    assert second_delivery == {
        "claimed": 1,
        "delivered": 1,
        "retried": 0,
        "failed": 0,
    }
    second_snapshot = (await client.get("/api/activation", headers=headers)).json()
    assert second_snapshot["stage"] == "forwarding_required"
    assert second_snapshot["number"]["assigned_e164"] != first_number
