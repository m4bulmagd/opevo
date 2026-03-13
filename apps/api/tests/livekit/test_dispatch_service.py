import json
from dataclasses import dataclass
from types import SimpleNamespace
from uuid import UUID
from uuid import uuid4

import pytest

from app.services.livekit_dispatch_service import LiveKitDispatchService


class FakeDispatchClient:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def create_dispatch(self, *, room_name: str, metadata: str) -> None:
        self.calls.append({"room_name": room_name, "metadata": metadata})


class FakeRealtimeService:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def publish_call_started(self, user_id: str, *, room_name: str, call_id: str) -> None:
        self.events.append({"user_id": user_id, "room_name": room_name, "call_id": call_id})


@dataclass
class FakePhoneNumber:
    id: UUID
    user_id: UUID
    e164: str


@dataclass
class FakeAgentConfig:
    id: UUID
    agent_name: str
    owner_context: str | None
    system_prompt: str
    knowledge_base: str
    pipeline_mode: str


@dataclass
class FakeUser:
    id: UUID
    full_name: str | None
    email: str


class FakePhoneNumberRepository:
    def __init__(self, phone_number: FakePhoneNumber) -> None:
        self.phone_number = phone_number

    async def get_by_e164(self, e164: str) -> FakePhoneNumber | None:
        if e164 == self.phone_number.e164:
            return self.phone_number
        return None


class FakeAgentConfigRepository:
    def __init__(self, agent_config: FakeAgentConfig) -> None:
        self.agent_config = agent_config

    async def get_by_user_id(self, user_id: UUID) -> FakeAgentConfig | None:
        return self.agent_config


class FakeCallRepository:
    def __init__(self, call_id: UUID) -> None:
        self.call = SimpleNamespace(id=call_id)

    async def create_pending(self, **kwargs):
        return self.call


class FakeUserRepository:
    def __init__(self, user: FakeUser) -> None:
        self.user = user

    async def get_by_id(self, user_id: UUID) -> FakeUser | None:
        return self.user


class FakeUsageRepository:
    async def get_current_balance(self, *, user_id) -> int:
        return 120


class FakeSession:
    def __init__(self) -> None:
        self.commits = 0

    async def commit(self) -> None:
        self.commits += 1


@pytest.mark.anyio
async def test_dispatch_service_includes_agent_runtime_configuration() -> None:
    user_id = uuid4()
    phone_number = FakePhoneNumber(id=uuid4(), user_id=user_id, e164="+33999888777")
    agent_config = FakeAgentConfig(
        id=uuid4(),
        agent_name="Ava",
        owner_context="Sam at Bakery",
        system_prompt="Be helpful and concise.",
        knowledge_base="Hours 9-5",
        pipeline_mode="stt_llm_tts",
    )
    user = FakeUser(id=user_id, full_name="Sam", email="active@example.com")

    dispatch_client = FakeDispatchClient()
    realtime_service = FakeRealtimeService()
    session = FakeSession()
    service = LiveKitDispatchService(
        session,
        dispatch_client=dispatch_client,
        realtime_service=realtime_service,
    )
    service.phone_number_repository = FakePhoneNumberRepository(phone_number)
    service.agent_config_repository = FakeAgentConfigRepository(agent_config)
    service.call_repository = FakeCallRepository(call_id=uuid4())
    service.user_repository = FakeUserRepository(user)
    service.usage_repository = FakeUsageRepository()

    await service.handle_participant_joined(
        {
            "event": "participant_joined",
            "room": {"name": "room_123"},
            "participant": {
                "attributes": {
                    "sip.phoneNumber": "+33123456789",
                    "sip.trunkPhoneNumber": "+33999888777",
                }
            },
        }
    )

    metadata = json.loads(dispatch_client.calls[0]["metadata"])
    assert metadata["agent_name"] == "Ava"
    assert metadata["owner_name"] == "Sam"
    assert metadata["system_prompt"] == "Be helpful and concise."
    assert metadata["knowledge_base"] == "Hours 9-5"
    assert metadata["pipeline_mode"] == "stt_llm_tts"
    assert metadata["minutes_remaining"] == 120
    assert realtime_service.events[0]["room_name"] == "room_123"
    assert session.commits == 1
