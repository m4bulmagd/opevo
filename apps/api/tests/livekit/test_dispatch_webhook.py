import asyncio
import json

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.agent_config import AgentConfig
from app.models.phone_number import PhoneNumber
from app.models.user import User


class FakeLiveKitReceiver:
    def receive(self, body: bytes, authorization: str | None) -> dict:
        return {
            "event": "participant_joined",
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


def test_participant_joined_dispatches_agent_and_creates_pending_call(
    client,
    client_database_url,
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

    asyncio.run(seed())

    from app.main import app
    from app.webhooks.livekit import get_dispatch_client, get_webhook_receiver

    dispatch_client = FakeDispatchClient()
    app.dependency_overrides[get_webhook_receiver] = lambda: FakeLiveKitReceiver()
    app.dependency_overrides[get_dispatch_client] = lambda: dispatch_client

    try:
        response = client.post(
            "/webhooks/livekit",
            content=json.dumps({"ignored": True}).encode("utf-8"),
            headers={"authorization": "Bearer test"},
        )
    finally:
        app.dependency_overrides.pop(get_webhook_receiver, None)
        app.dependency_overrides.pop(get_dispatch_client, None)

    assert response.status_code == 202
    assert dispatch_client.calls[0]["room_name"] == "room_123"
