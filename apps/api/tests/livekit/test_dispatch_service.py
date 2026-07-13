import json
import logging
from dataclasses import dataclass
from types import SimpleNamespace
from uuid import UUID
from uuid import uuid4

import pytest

from app.services.livekit_dispatch_service import LiveKitDispatchService


ROOM_NAME_SENTINEL = "room_TRANSCRIPT_SENTINEL_+33612345678"
RECORDING_START_SENTINEL = "RECORDING_START_AUTHORIZATION_SENTINEL"
RECORDING_STOP_SENTINEL = "RECORDING_STOP_TRANSCRIPT_SENTINEL"


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

    async def get_by_any_format(self, raw_number: str) -> FakePhoneNumber | None:
        normalized = "".join(ch for ch in raw_number if ch.isdigit())
        stored = "".join(ch for ch in self.phone_number.e164 if ch.isdigit())
        if normalized == stored:
            return self.phone_number
        return None


class FakeAgentConfigRepository:
    def __init__(self, agent_config: FakeAgentConfig) -> None:
        self.agent_config = agent_config

    async def get_by_user_id(self, user_id: UUID) -> FakeAgentConfig | None:
        return self.agent_config


class FakeCallRepository:
    def __init__(self, call_id: UUID) -> None:
        self.call = SimpleNamespace(
            id=call_id,
            user_id=uuid4(),
            livekit_room_id="room_123",
            status="pending",
            recording_url=None,
            recording_object_key=None,
            recording_egress_id=None,
        )
        self.recording_metadata_calls: list[dict] = []
        self.pending_by_room_calls: list[str] = []
        self.active_by_room_calls: list[str] = []

    async def create_pending(self, **kwargs):
        self.call.user_id = kwargs["user_id"]
        self.call.livekit_room_id = kwargs["livekit_room_id"]
        return self.call

    async def get_pending_by_room_without_recording(self, *, room_name: str):
        self.pending_by_room_calls.append(room_name)
        if self.call.livekit_room_id != room_name:
            return None
        if self.call.status != "pending":
            return None
        if self.call.recording_egress_id is not None:
            return None
        return self.call

    async def connect_if_pending(self, *, call_id: UUID):
        if self.call.id != call_id or self.call.status != "pending":
            return None
        self.call.status = "connected"
        return self.call

    async def get_by_id_without_recording_for_update(
        self,
        *,
        call_id: UUID,
    ):
        if (
            self.call.id != call_id
            or self.call.recording_egress_id is not None
        ):
            return None
        return self.call

    async def get_active_by_room_with_recording(self, *, room_name: str):
        self.active_by_room_calls.append(room_name)
        if self.call.livekit_room_id != room_name:
            return None
        if self.call.recording_egress_id is None:
            return None
        return self.call

    async def get_active_by_room_for_update(self, *, room_name: str):
        self.active_by_room_calls.append(room_name)
        if self.call.livekit_room_id != room_name:
            return None
        if self.call.status not in {"pending", "connected", "ending", "finalizing"}:
            return None
        return self.call

    async def set_recording_metadata(
        self,
        call,
        *,
        recording_object_key: str,
        recording_egress_id: str,
        recording_url: str | None,
    ):
        call.recording_object_key = recording_object_key
        call.recording_egress_id = recording_egress_id
        call.recording_url = recording_url
        self.recording_metadata_calls.append(
            {
                "call": call,
                "recording_object_key": recording_object_key,
                "recording_egress_id": recording_egress_id,
                "recording_url": recording_url,
            }
        )
        return call


class FakeUserRepository:
    def __init__(self, user: FakeUser) -> None:
        self.user = user

    async def get_by_id(self, user_id: UUID) -> FakeUser | None:
        return self.user


class FakeUsageRepository:
    async def get_current_balance(self, *, user_id) -> int:
        return 120


class FakeRecordingService:
    def __init__(self) -> None:
        self.start_calls: list[dict] = []
        self.stop_calls: list[str] = []

    async def start_room_recording(self, *, room_name: str, user_id: UUID, call_id: UUID):
        self.start_calls.append(
            {
                "room_name": room_name,
                "user_id": user_id,
                "call_id": call_id,
            }
        )
        return SimpleNamespace(
            object_key=f"calls/{user_id}/{call_id}.ogg",
            egress_id="egress_123",
            url=f"http://minio:9000/recordings/calls/{user_id}/{call_id}.ogg",
        )

    async def stop_room_recording(self, *, egress_id: str):
        self.stop_calls.append(egress_id)


class FakeFailingRecordingService:
    async def start_room_recording(self, *, room_name: str, user_id: UUID, call_id: UUID):
        raise RuntimeError(RECORDING_START_SENTINEL)


class FakeFailingStopRecordingService(FakeRecordingService):
    async def stop_room_recording(self, *, egress_id: str):
        raise RuntimeError(RECORDING_STOP_SENTINEL)


class FakeSession:
    def __init__(self) -> None:
        self.commits = 0

    async def commit(self) -> None:
        self.commits += 1


class FakeCallLifecycleService:
    def __init__(self, call_repository: FakeCallRepository) -> None:
        self.call_repository = call_repository
        self.end_calls: list[UUID] = []

    async def end_from_sip(self, *, call_id: UUID, ended_at):
        self.end_calls.append(call_id)
        call = self.call_repository.call
        if call.status == "pending":
            call.status = "failed"
            call.failure_code = "caller_left_before_connect"
        elif call.status == "connected":
            call.status = "ending"
        return call


def build_dispatch_service(
    session,
    dispatch_client,
    *,
    phone_number_repository=None,
    agent_config_repository=None,
    call_repository=None,
    user_repository=None,
    usage_repository=None,
    realtime_service=None,
    recording_service=None,
    call_lifecycle_service=None,
) -> LiveKitDispatchService:
    resolved_call_repository = call_repository or FakeCallRepository(call_id=uuid4())
    return LiveKitDispatchService(
        session,
        dispatch_client,
        phone_number_repository=phone_number_repository or FakePhoneNumberRepository(FakePhoneNumber(id=uuid4(), user_id=uuid4(), e164="+33000000000")),
        agent_config_repository=agent_config_repository or FakeAgentConfigRepository(FakeAgentConfig(id=uuid4(), agent_name="A", owner_context=None, system_prompt="", knowledge_base="", pipeline_mode="stt_llm_tts")),
        call_repository=resolved_call_repository,
        user_repository=user_repository or FakeUserRepository(FakeUser(id=uuid4(), full_name=None, email="x@x.com")),
        usage_repository=usage_repository or FakeUsageRepository(),
        realtime_service=realtime_service or FakeRealtimeService(),
        recording_service=recording_service or FakeRecordingService(),
        call_lifecycle_service=(
            call_lifecycle_service
            or FakeCallLifecycleService(resolved_call_repository)
        ),
    )


@pytest.mark.anyio
async def test_dispatch_service_persists_recording_metadata_when_egress_starts() -> None:
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
    recording_service = FakeRecordingService()
    session = FakeSession()
    call_repository = FakeCallRepository(call_id=uuid4())
    call_repository.call.user_id = user_id
    service = build_dispatch_service(
        session,
        dispatch_client,
        phone_number_repository=FakePhoneNumberRepository(phone_number),
        agent_config_repository=FakeAgentConfigRepository(agent_config),
        call_repository=call_repository,
        user_repository=FakeUserRepository(user),
        recording_service=recording_service,
    )

    await service.handle_participant_joined(
        {
            "event": "participant_joined",
            "room": {"name": "room_123"},
            "participant": {
                "identity": f"agent-call-{call_repository.call.id}",
                "kind": "AGENT",
                "attributes": {},
            },
        }
    )

    assert recording_service.start_calls == [
        {
            "room_name": "room_123",
            "user_id": user_id,
            "call_id": call_repository.call.id,
        }
    ]
    assert call_repository.pending_by_room_calls == ["room_123"]
    assert call_repository.recording_metadata_calls == [
        {
            "call": call_repository.call,
            "recording_object_key": f"calls/{user_id}/{call_repository.call.id}.ogg",
            "recording_egress_id": "egress_123",
            "recording_url": f"http://minio:9000/recordings/calls/{user_id}/{call_repository.call.id}.ogg",
        }
    ]


@pytest.mark.anyio
async def test_dispatch_service_skips_agent_join_when_recording_already_started() -> None:
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
    recording_service = FakeRecordingService()
    session = FakeSession()
    call_repository = FakeCallRepository(call_id=uuid4())
    call_repository.call.recording_egress_id = "egress_existing"
    service = build_dispatch_service(
        session,
        dispatch_client,
        phone_number_repository=FakePhoneNumberRepository(phone_number),
        agent_config_repository=FakeAgentConfigRepository(agent_config),
        call_repository=call_repository,
        user_repository=FakeUserRepository(user),
        recording_service=recording_service,
    )

    await service.handle_participant_joined(
        {
            "event": "participant_joined",
            "room": {"name": "room_123"},
            "participant": {
                "identity": f"agent-call-{call_repository.call.id}",
                "kind": "AGENT",
                "attributes": {},
            },
        }
    )

    assert recording_service.start_calls == []
    assert call_repository.pending_by_room_calls == ["room_123"]
    assert call_repository.recording_metadata_calls == []


@pytest.mark.anyio
async def test_dispatch_service_ends_call_without_direct_provider_stop_on_sip_leave() -> None:
    recording_service = FakeRecordingService()
    session = FakeSession()
    call_repository = FakeCallRepository(call_id=uuid4())
    call_repository.call.status = "connected"
    call_repository.call.recording_egress_id = "egress_123"
    service = build_dispatch_service(
        session,
        FakeDispatchClient(),
        call_repository=call_repository,
        recording_service=recording_service,
    )

    result = await service.handle_participant_left(
        {
            "event": "participant_left",
            "room": {"name": "room_123"},
            "participant": {
                "identity": "sip_caller",
                "kind": "SIP",
                "attributes": {
                    "sip.phoneNumber": "+33123456789",
                },
            },
        }
    )

    assert result.status == "ending"
    assert recording_service.stop_calls == []
    assert call_repository.active_by_room_calls == ["room_123"]


@pytest.mark.anyio
async def test_dispatch_service_does_not_call_recording_provider_during_sip_leave(caplog) -> None:
    session = FakeSession()
    call_repository = FakeCallRepository(call_id=uuid4())
    call_repository.call.status = "connected"
    call_repository.call.livekit_room_id = ROOM_NAME_SENTINEL
    call_repository.call.recording_egress_id = "egress_123"
    service = build_dispatch_service(
        session,
        FakeDispatchClient(),
        call_repository=call_repository,
        recording_service=FakeFailingStopRecordingService(),
    )

    with caplog.at_level(logging.ERROR):
        await service.handle_participant_left(
            {
                "event": "participant_left",
                "room": {"name": ROOM_NAME_SENTINEL},
                "participant": {
                    "identity": "sip_caller",
                    "kind": "SIP",
                    "attributes": {
                        "sip.phoneNumber": "+33123456789",
                    },
                },
            }
        )

    assert call_repository.active_by_room_calls == [ROOM_NAME_SENTINEL]
    assert session.commits == 1
    assert RECORDING_STOP_SENTINEL not in caplog.text
    assert ROOM_NAME_SENTINEL not in caplog.text
    assert "livekit_recording_stop_failed" not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


@pytest.mark.anyio
async def test_dispatch_service_continues_when_recording_egress_fails(caplog) -> None:
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
    session = FakeSession()
    call_repository = FakeCallRepository(call_id=uuid4())
    call_repository.call.user_id = user_id
    call_repository.call.livekit_room_id = ROOM_NAME_SENTINEL
    service = build_dispatch_service(
        session,
        dispatch_client,
        phone_number_repository=FakePhoneNumberRepository(phone_number),
        agent_config_repository=FakeAgentConfigRepository(agent_config),
        call_repository=call_repository,
        user_repository=FakeUserRepository(user),
        recording_service=FakeFailingRecordingService(),
    )

    with caplog.at_level(logging.ERROR):
        await service.handle_participant_joined(
            {
                "event": "participant_joined",
                "room": {"name": ROOM_NAME_SENTINEL},
                "participant": {
                    "identity": f"agent-call-{call_repository.call.id}",
                    "kind": "AGENT",
                    "attributes": {},
                },
            }
        )

    assert dispatch_client.calls == []
    assert call_repository.pending_by_room_calls == [ROOM_NAME_SENTINEL]
    assert call_repository.recording_metadata_calls == []
    assert RECORDING_START_SENTINEL not in caplog.text
    assert ROOM_NAME_SENTINEL not in caplog.text
    assert "event=livekit_recording_start_failed" in caplog.text
    assert "operation=start_room_recording" in caplog.text
    assert f"call_id={call_repository.call.id}" in caplog.text
    assert f"user_id={user_id}" in caplog.text
    assert all(record.exc_info is None for record in caplog.records)
