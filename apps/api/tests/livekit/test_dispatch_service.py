import json

import pytest

from app.models.agent_config import AgentConfig
from app.models.phone_number import PhoneNumber
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


@pytest.mark.anyio
async def test_dispatch_service_includes_agent_runtime_configuration(
    db_session,
    active_user,
) -> None:
    db_session.add(
        PhoneNumber(
            user_id=active_user.id,
            e164="+33999888777",
            country_code="FR",
            provider="telnyx",
            provider_number_id="tnx_123",
            provider_connection_name="app-active",
            is_active=True,
        )
    )
    db_session.add(
        AgentConfig(
            user_id=active_user.id,
            agent_name="Ava",
            owner_context="Sam at Bakery",
            system_prompt="Be helpful and concise.",
            knowledge_base="Hours 9-5",
            pipeline_mode="stt_llm_tts",
            is_enabled=True,
        )
    )
    active_user.full_name = "Sam"
    await db_session.commit()

    dispatch_client = FakeDispatchClient()
    realtime_service = FakeRealtimeService()
    service = LiveKitDispatchService(
        db_session,
        dispatch_client=dispatch_client,
        realtime_service=realtime_service,
    )

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
