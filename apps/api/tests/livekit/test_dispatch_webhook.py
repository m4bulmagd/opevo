import json
import logging

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.agent_config import AgentConfig
from app.models.phone_number import PhoneNumber
from app.models.user import User
from app.services.livekit_dispatch_service import LiveKitDispatchService


ROOM_NAME_SENTINEL = "room_TRANSCRIPT_SENTINEL_+33612345678"


class FakeLiveKitReceiver:
    def receive(self, body: bytes, authorization: str | None) -> dict:
        return {
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


class FakeParticipantLeftReceiver:
    def receive(self, body: bytes, authorization: str | None) -> dict:
        return {
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

    async def publish_call_started(self, user_id: str, *, room_name: str, call_id: str) -> None:
        self.call_started_events.append(
            {"user_id": user_id, "room_name": room_name, "call_id": call_id}
        )


class FakeWebhookRequest:
    headers = {"authorization": "Bearer test"}

    async def body(self) -> bytes:
        return b"{}"


class FakeRoomSentinelReceiver:
    def receive(self, body: bytes, authorization: str | None) -> dict:
        return {
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
async def test_webhook_log_does_not_render_provider_controlled_room_name(caplog) -> None:
    from app.webhooks.livekit import handle_livekit_webhook

    session = FakeWebhookSession()
    with caplog.at_level(logging.INFO):
        response = await handle_livekit_webhook(
            FakeWebhookRequest(),
            session=session,
            webhook_receiver=FakeRoomSentinelReceiver(),
            dispatch_client=FakeDispatchClient(),
            realtime_service=FakeRealtimeService(),
        )

    assert response.status_code == 202
    assert session.commits == 1
    assert ROOM_NAME_SENTINEL not in caplog.text
    assert "livekit webhook received event=room_finished" in caplog.text


@pytest.mark.anyio
async def test_participant_joined_dispatches_agent_and_creates_pending_call(
    async_client,
    client_database_url,
    caplog,
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
                    system_prompt="Be helpful",
                    knowledge_base="Hours 9-5",
                    pipeline_mode="stt_llm_tts",
                    is_enabled=True,
                )
            )
            await session.commit()
        await engine.dispose()

    await seed()

    from app.main import app
    from app.webhooks.livekit import (
        get_dispatch_client,
        get_realtime_service,
        get_webhook_receiver,
    )

    dispatch_client = FakeDispatchClient()
    realtime_service = FakeRealtimeService()
    app.dependency_overrides[get_webhook_receiver] = lambda: FakeLiveKitReceiver()
    app.dependency_overrides[get_dispatch_client] = lambda: dispatch_client
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
        app.dependency_overrides.pop(get_dispatch_client, None)
        app.dependency_overrides.pop(get_realtime_service, None)

    metadata = json.loads(dispatch_client.calls[0]["metadata"])
    assert response.status_code == 202
    assert dispatch_client.calls[0]["room_name"] == ROOM_NAME_SENTINEL
    assert realtime_service.call_started_events[0]["user_id"] == metadata["user_id"]
    assert realtime_service.call_started_events[0]["call_id"] == metadata["call_id"]
    assert realtime_service.call_started_events[0]["room_name"] == ROOM_NAME_SENTINEL
    assert ROOM_NAME_SENTINEL not in caplog.text
    assert metadata["call_id"] in caplog.text
    assert metadata["user_id"] in caplog.text
    assert "SIP_ATTRIBUTE_SENTINEL_SECRET" not in caplog.text
    assert "+33123456789" not in caplog.text
    assert "+33999888777" not in caplog.text
    assert "+33******89" in caplog.text
    assert "+33******77" in caplog.text


@pytest.mark.anyio
async def test_participant_left_routes_to_leave_handler(
    async_client,
    monkeypatch,
) -> None:
    from app.main import app
    from app.webhooks.livekit import (
        get_dispatch_client,
        get_realtime_service,
        get_webhook_receiver,
    )

    dispatch_client = FakeDispatchClient()
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
    app.dependency_overrides[get_dispatch_client] = lambda: dispatch_client
    app.dependency_overrides[get_realtime_service] = lambda: realtime_service

    try:
        response = await async_client.post(
            "/webhooks/livekit",
            content=json.dumps({"ignored": True}).encode("utf-8"),
            headers={"authorization": "Bearer test"},
        )
    finally:
        app.dependency_overrides.pop(get_webhook_receiver, None)
        app.dependency_overrides.pop(get_dispatch_client, None)
        app.dependency_overrides.pop(get_realtime_service, None)

    assert response.status_code == 202
    assert observed == [
        {
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
