from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID

import httpx
import pytest
import pytest_asyncio
from fastapi import Depends, FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import Settings
from app.core.database import get_session
from app.models import Base
from app.models.call import Call
from app.models.customer_activation import CustomerActivation
from app.models.notification import Notification
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
from app.workers.outbox.delivery import outbox_delivery_job


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
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[
    tuple[httpx.AsyncClient, async_sessionmaker[AsyncSession], FastAPI]
]:
    from app import main as main_module

    class NoopPool:
        async def aclose(self) -> None:
            pass

    async def create_pool(redis_url: str) -> NoopPool:
        del redis_url
        return NoopPool()

    monkeypatch.setattr(main_module, "create_arq_pool", create_pool)

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
    application = main_module.create_app(settings)

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
        async with application.router.lifespan_context(application):
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://testserver",
            ) as client:
                yield client, session_factory, application
    finally:
        application.dependency_overrides.clear()
        await engine.dispose()


@pytest.mark.anyio
async def test_provider_free_journey_reaches_forwarding_required(
    local_client: tuple[
        httpx.AsyncClient,
        async_sessionmaker[AsyncSession],
        FastAPI,
    ],
) -> None:
    client, session_factory, _application = local_client
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
    reactivation_snapshot = reactivated.json()
    assert reactivation_snapshot["stage"] == "provisioning_consent_required"
    assert reactivation_snapshot["profile"]["business_name"] == "Atelier Martin"
    assert reactivation_snapshot["profile"]["receptionist_name"] == "Léa"
    assert reactivation_snapshot["profile"]["confirmed_carrier"] == "orange"
    assert reactivation_snapshot["activation"]["profile_confirmed_at"] is not None
    assert reactivation_snapshot["activation"]["provisioning_consented_at"] is None
    assert reactivation_snapshot["number"]["assigned_e164"] is None

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


@pytest.mark.anyio
async def test_call_drain_fixture_uses_real_owner_scoped_call_lifecycle(
    local_client: tuple[
        httpx.AsyncClient,
        async_sessionmaker[AsyncSession],
        FastAPI,
    ],
) -> None:
    client, session_factory, _application = local_client
    headers = {"Authorization": f"Bearer {LOCAL_TOKEN}"}

    account = await client.get("/api/account", headers=headers)
    assert account.status_code == 200

    async with session_factory() as session:
        local_user = await session.scalar(
            select(User).where(User.clerk_user_id == "local_presvo_user")
        )
        assert local_user is not None
        session.add(
            UsageLedger(
                user_id=local_user.id,
                event_type="subscription_activated",
                source_id="local_call_drain_fixture_grant",
                minutes_delta=10,
                balance_after=10,
            )
        )
        await session.commit()

    unauthenticated = await client.post(
        "/api/development/call-drain-fixture/start"
    )
    assert unauthenticated.status_code == 401
    unauthenticated_finish = await client.post(
        "/api/development/call-drain-fixture/finish",
        json={"call_id": "00000000-0000-0000-0000-000000000000"},
    )
    assert unauthenticated_finish.status_code == 401

    started = await client.post(
        "/api/development/call-drain-fixture/start",
        headers=headers,
    )
    assert started.status_code == 200
    assert set(started.json()) == {"call_id"}
    call_id = UUID(started.json()["call_id"])

    async with session_factory() as session:
        call = await session.get(Call, call_id)
        assert call is not None
        assert call.user_id == local_user.id
        assert call.status == "connected"
        assert call.livekit_room_id is None
        assert call.livekit_dispatch_id is None

    visible_while_connected = await client.get("/api/calls", headers=headers)
    assert visible_while_connected.status_code == 200
    assert [
        (item["id"], item["status"])
        for item in visible_while_connected.json()["calls"]
    ] == [(str(call_id), "connected")]

    finished = await client.post(
        "/api/development/call-drain-fixture/finish",
        headers=headers,
        json={"call_id": str(call_id)},
    )
    assert finished.status_code == 200
    assert finished.json() == {"call_id": str(call_id)}

    async with session_factory() as session:
        call = await session.get(Call, call_id)
        assert call is not None
        assert call.status == "completed"
        assert call.duration_seconds == 1
        assert call.minutes_charged == 1
        notification_count = await session.scalar(
            select(func.count(Notification.id)).where(
                Notification.call_id == call.id
            )
        )
        summary_event = await session.scalar(
            select(OutboxEvent).where(
                OutboxEvent.topic == "summary.generate",
                OutboxEvent.aggregate_id == call.id,
            )
        )
        assert notification_count == 1
        assert summary_event is not None
        assert summary_event.payload == {"call_id": str(call_id)}

    visible_after_completion = await client.get("/api/calls", headers=headers)
    assert visible_after_completion.status_code == 200
    assert [
        (item["id"], item["status"])
        for item in visible_after_completion.json()["calls"]
    ] == [(str(call_id), "completed")]


@pytest.mark.anyio
async def test_call_drain_fixture_rejects_non_fake_telephony(
    local_client: tuple[
        httpx.AsyncClient,
        async_sessionmaker[AsyncSession],
        FastAPI,
    ],
) -> None:
    client, _session_factory, application = local_client
    application.state.settings.telephony_mode = "telnyx"

    response = await client.post(
        "/api/development/call-drain-fixture/start",
        headers={"Authorization": f"Bearer {LOCAL_TOKEN}"},
    )

    assert response.status_code == 409
    assert response.json() == {
        "detail": {"code": "local_telephony_disabled"}
    }


@pytest.mark.anyio
async def test_call_drain_fixture_rejects_clerk_auth_before_mutation(
    local_client: tuple[
        httpx.AsyncClient,
        async_sessionmaker[AsyncSession],
        FastAPI,
    ],
) -> None:
    from app.core.auth import AuthProvider, UserIdentity, get_auth_provider

    client, session_factory, application = local_client
    async with session_factory() as session:
        clerk_user = User(
            clerk_user_id="clerk_fixture_owner",
            email="clerk-fixture-owner@example.invalid",
        )
        session.add(clerk_user)
        await session.commit()

    class AuthenticatedClerkProvider(AuthProvider):
        async def verify_token(self, token: str) -> UserIdentity:
            assert token == "authenticated-clerk-fixture-test-token"
            return UserIdentity(clerk_user_id=clerk_user.clerk_user_id)

    def clerk_auth_provider() -> AuthProvider:
        return AuthenticatedClerkProvider()

    application.state.settings.auth_mode = "clerk"
    application.dependency_overrides[get_auth_provider] = clerk_auth_provider
    clerk_headers = {
        "Authorization": "Bearer authenticated-clerk-fixture-test-token"
    }
    try:
        started = await client.post(
            "/api/development/call-drain-fixture/start",
            headers=clerk_headers,
        )
        async with session_factory() as session:
            existing_call = await session.scalar(
                select(Call).where(Call.user_id == clerk_user.id)
            )
            if existing_call is None:
                existing_call = Call(
                    user_id=clerk_user.id,
                    status="connected",
                )
                session.add(existing_call)
                await session.commit()
            existing_call_id = existing_call.id
        finished = await client.post(
            "/api/development/call-drain-fixture/finish",
            headers=clerk_headers,
            json={"call_id": str(existing_call_id)},
        )
    finally:
        application.dependency_overrides.pop(get_auth_provider, None)
        application.state.settings.auth_mode = "local"

    unavailable = {"detail": {"code": "local_telephony_disabled"}}
    assert started.status_code == 409
    assert started.json() == unavailable
    assert finished.status_code == 409
    assert finished.json() == unavailable

    async with session_factory() as session:
        calls = list(
            (
                await session.execute(
                    select(Call).where(Call.user_id == clerk_user.id)
                )
            ).scalars()
        )
        assert [(call.id, call.status) for call in calls] == [
            (existing_call_id, "connected")
        ]


@pytest.mark.anyio
async def test_call_drain_fixture_hides_foreign_calls(
    local_client: tuple[
        httpx.AsyncClient,
        async_sessionmaker[AsyncSession],
        FastAPI,
    ],
) -> None:
    client, session_factory, _application = local_client
    headers = {"Authorization": f"Bearer {LOCAL_TOKEN}"}

    account = await client.get("/api/account", headers=headers)
    assert account.status_code == 200

    async with session_factory() as session:
        foreign_user = User(
            clerk_user_id="foreign_call_drain_owner",
            email="foreign-call-drain-owner@example.invalid",
        )
        session.add(foreign_user)
        await session.flush()
        foreign_call = Call(
            user_id=foreign_user.id,
            status="connected",
        )
        session.add(foreign_call)
        await session.commit()
        foreign_call_id = foreign_call.id

    response = await client.post(
        "/api/development/call-drain-fixture/finish",
        headers=headers,
        json={"call_id": str(foreign_call_id)},
    )

    assert response.status_code == 404
    async with session_factory() as session:
        foreign_call = await session.get(Call, foreign_call_id)
        assert foreign_call is not None
        assert foreign_call.status == "connected"


@pytest.mark.anyio
async def test_call_drain_fixture_routes_are_absent_outside_development(
    tmp_path: Path,
) -> None:
    from app.main import create_app

    application = create_app(
        Settings(
            app_env="test",
            database_url=(
                f"sqlite+aiosqlite:///{tmp_path / 'non-development.db'}"
            ),
            redis_url="redis://localhost:6379/0",
            auth_mode="clerk",
            agent_dispatch_jwt_secret="a" * 32,
        )
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application),
        base_url="http://testserver",
    ) as client:
        start = await client.post(
            "/api/development/call-drain-fixture/start"
        )
        finish = await client.post(
            "/api/development/call-drain-fixture/finish",
            json={"call_id": "00000000-0000-0000-0000-000000000000"},
        )

    assert start.status_code == 404
    assert finish.status_code == 404
