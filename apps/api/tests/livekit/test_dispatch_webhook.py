import json
import logging
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from conftest import install_test_api_runtime
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.agent_config import AgentConfig
from app.models.call import Call
from app.models.outbox_event import OutboxEvent
from app.models.phone_number import PhoneNumber
from app.models.subscription import Subscription
from app.models.user import User
from app.models.usage_ledger import UsageLedger
from app.services.livekit_dispatch_service import LiveKitDispatchService


ROOM_NAME_SENTINEL = "room_TRANSCRIPT_SENTINEL_+33612345678"


class FakeLiveKitReceiver:
    def receive(self, body: bytes, authorization: str | None) -> dict:
        return {
            "id": "EV_joined",
            "event": "participant_joined",
            "room": {"name": ROOM_NAME_SENTINEL},
            "participant": {
                "identity": "sip_caller",
                "kind": "SIP",
                "attributes": {
                    "sip.phoneNumber": "+33123456789",
                    "sip.trunkPhoneNumber": "+33999888777",
                    "sip.authorization": "SIP_ATTRIBUTE_SENTINEL_SECRET",
                },
            },
        }


class FakeLocalFrenchTrunkReceiver(FakeLiveKitReceiver):
    def receive(self, body: bytes, authorization: str | None) -> dict:
        event = super().receive(body, authorization)
        event["participant"]["attributes"]["sip.trunkPhoneNumber"] = (
            "09 99 88 87 77"
        )
        return event


class FakeParticipantLeftReceiver:
    def receive(self, body: bytes, authorization: str | None) -> dict:
        return {
            "id": "EV_left",
            "event": "participant_left",
            "room": {"name": "room_123"},
            "participant": {
                "identity": "sip_caller",
                "kind": "SIP",
                "attributes": {
                    "sip.phoneNumber": "+33123456789",
                    "sip.trunkPhoneNumber": "+33999888777",
                },
            },
        }


class FakeDispatchClient:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def create_dispatch(self, *, room_name: str, metadata: str) -> None:
        self.calls.append({"room_name": room_name, "metadata": metadata})


class FakeRealtimeService:
    def __init__(self) -> None:
        self.call_started_events: list[dict] = []

    async def publish_call_started(self, user_id, *, room_name: str, call_id) -> None:
        self.call_started_events.append(
            {"user_id": user_id, "room_name": room_name, "call_id": call_id}
        )


class FakeWebhookRequest:
    headers = {"authorization": "Bearer test"}

    def __init__(self) -> None:
        self.app = SimpleNamespace(state=SimpleNamespace())
        install_test_api_runtime(self.app)

    async def body(self) -> bytes:
        return b"{}"


class FakeRoomSentinelReceiver:
    def receive(self, body: bytes, authorization: str | None) -> dict:
        return {
            "id": "EV_room_finished",
            "event": "room_finished",
            "room": {"name": ROOM_NAME_SENTINEL},
            "participant": {"kind": "STANDARD", "attributes": {}},
        }


class FakeWebhookSession:
    def __init__(self) -> None:
        self.commits = 0

    async def commit(self) -> None:
        self.commits += 1


@pytest.mark.anyio
async def test_webhook_log_does_not_render_provider_controlled_room_name(
    db_session,
    caplog,
) -> None:
    from app.webhooks.livekit import handle_livekit_webhook

    with caplog.at_level(logging.INFO):
        response = await handle_livekit_webhook(
            FakeWebhookRequest(),
            session=db_session,
            webhook_receiver=FakeRoomSentinelReceiver(),
            realtime_service=FakeRealtimeService(),
        )

    assert response.status_code == 202
    assert ROOM_NAME_SENTINEL not in caplog.text
    assert "livekit webhook received event=room_finished" in caplog.text


@pytest.mark.anyio
@pytest.mark.parametrize(
    "receiver_type",
    [FakeLiveKitReceiver, FakeLocalFrenchTrunkReceiver],
)
async def test_participant_joined_dispatches_agent_and_creates_pending_call(
    async_client,
    client_database_url,
    caplog,
    receiver_type,
    configured_livekit_recording_runtime,
) -> None:
    async def seed() -> None:
        engine = create_async_engine(client_database_url, future=True)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session:
            user = User(clerk_user_id="user_livekit", email="livekit@example.com")
            session.add(user)
            await session.flush()
            session.add(
                PhoneNumber(
                    user_id=user.id,
                    e164="+33999888777",
                    country_code="FR",
                    provider="telnyx",
                    provider_number_id="tnx_123",
                    provider_connection_name="app-active",
                    is_active=True,
                )
            )
            session.add(
                AgentConfig(
                    user_id=user.id,
                    agent_name="Ava",
                    owner_context="Sam at Bakery",
                    system_prompt="Be helpful",
                    knowledge_base="Hours 9-5",
                    pipeline_mode="stt_llm_tts",
                    is_enabled=True,
                )
            )
            now = datetime.now(UTC)
            session.add_all(
                [
                    Subscription(
                        user_id=user.id,
                        stripe_customer_id="cus-livekit",
                        stripe_subscription_id="sub-livekit",
                        plan_tier="starter",
                        status="active",
                        allocated_minutes=60,
                        current_period_start=now - timedelta(days=1),
                        current_period_end=now + timedelta(days=1),
                    ),
                    UsageLedger(
                        user_id=user.id,
                        event_type="invoice_paid_reset",
                        source_id="invoice-livekit",
                        minutes_delta=60,
                        balance_after=60,
                    ),
                ]
            )
            await session.commit()
        await engine.dispose()

    await seed()

    from app.main import app
    from app.webhooks.livekit import get_realtime_service, get_webhook_receiver

    realtime_service = FakeRealtimeService()
    app.dependency_overrides[get_webhook_receiver] = receiver_type
    app.dependency_overrides[get_realtime_service] = lambda: realtime_service

    try:
        with caplog.at_level(logging.INFO):
            response = await async_client.post(
                "/webhooks/livekit",
                content=json.dumps({"ignored": True}).encode("utf-8"),
                headers={"authorization": "Bearer test"},
            )
    finally:
        app.dependency_overrides.pop(get_webhook_receiver, None)
        app.dependency_overrides.pop(get_realtime_service, None)

    engine = create_async_engine(client_database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        call = await session.scalar(select(Call))
        outbox = await session.scalar(select(OutboxEvent))
    await engine.dispose()
    assert call is not None
    assert outbox is not None
    assert response.status_code == 202
    assert outbox.payload == {
        "call_id": str(call.id),
        "lifecycle_generation": 1,
    }
    assert realtime_service.call_started_events[0]["user_id"] == call.user_id
    assert realtime_service.call_started_events[0]["call_id"] == call.id
    assert realtime_service.call_started_events[0]["room_name"] == ROOM_NAME_SENTINEL
    assert ROOM_NAME_SENTINEL not in caplog.text
    assert "SIP_ATTRIBUTE_SENTINEL_SECRET" not in caplog.text
    assert "+33123456789" not in caplog.text
    assert "+33999888777" not in caplog.text


@pytest.mark.anyio
async def test_participant_left_routes_to_leave_handler(
    async_client,
    monkeypatch,
    configured_livekit_recording_runtime,
) -> None:
    from app.main import app
    from app.webhooks.livekit import get_realtime_service, get_webhook_receiver

    realtime_service = FakeRealtimeService()
    observed: list[dict] = []

    async def fake_handle_participant_left(self, event: dict) -> None:
        observed.append(event)
        await self.session.commit()

    monkeypatch.setattr(
        LiveKitDispatchService,
        "handle_participant_left",
        fake_handle_participant_left,
        raising=False,
    )

    app.dependency_overrides[get_webhook_receiver] = lambda: FakeParticipantLeftReceiver()
    app.dependency_overrides[get_realtime_service] = lambda: realtime_service

    try:
        response = await async_client.post(
            "/webhooks/livekit",
            content=json.dumps({"ignored": True}).encode("utf-8"),
            headers={"authorization": "Bearer test"},
        )
    finally:
        app.dependency_overrides.pop(get_webhook_receiver, None)
        app.dependency_overrides.pop(get_realtime_service, None)

    assert response.status_code == 202
    assert observed == [
        {
            "id": "EV_left",
            "event": "participant_left",
            "room": {"name": "room_123"},
            "participant": {
                "identity": "sip_caller",
                "kind": "SIP",
                "attributes": {
                    "sip.phoneNumber": "+33123456789",
                    "sip.trunkPhoneNumber": "+33999888777",
                },
            },
        }
    ]


@pytest.mark.anyio
async def test_disabled_app_webhook_injects_no_realtime_service(
    async_client,
    monkeypatch,
    configured_livekit_recording_runtime,
) -> None:
    from app.main import app
    from app.webhooks.livekit import get_webhook_receiver

    observed: list[object] = []

    async def fake_handle_participant_left(self, event: dict) -> None:
        observed.append(self.realtime_service)
        await self.session.commit()

    monkeypatch.setattr(
        LiveKitDispatchService,
        "handle_participant_left",
        fake_handle_participant_left,
    )
    app.dependency_overrides[get_webhook_receiver] = lambda: FakeParticipantLeftReceiver()
    try:
        response = await async_client.post(
            "/webhooks/livekit",
            content=json.dumps({"ignored": True}).encode("utf-8"),
            headers={"authorization": "Bearer test"},
        )
    finally:
        app.dependency_overrides.pop(get_webhook_receiver, None)

    assert response.status_code == 202
    assert observed == [None]
