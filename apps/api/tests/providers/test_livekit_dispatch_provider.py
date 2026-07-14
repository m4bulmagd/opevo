from types import SimpleNamespace
from contextlib import asynccontextmanager

import pytest

from app.providers.livekit_dispatch.livekit import LiveKitDispatchAPIProvider


class _Telemetry:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    @asynccontextmanager
    async def provider_operation(self, provider: str, operation: str, **_kwargs):
        try:
            yield
        except Exception:
            self.calls.append((provider, operation, "error"))
            raise
        else:
            self.calls.append((provider, operation, "success"))


class _DispatchService:
    def __init__(self) -> None:
        self.list_rooms: list[str] = []
        self.create_requests: list[object] = []

    async def list_dispatch(self, room_name: str):
        self.list_rooms.append(room_name)
        return [
            SimpleNamespace(
                id="dispatch-existing",
                agent_name="Ava",
                room=room_name,
                metadata='{"call_id":"call-1"}',
                state=1,
            )
        ]

    async def create_dispatch(self, request):
        self.create_requests.append(request)
        return SimpleNamespace(
            id="dispatch-created",
            agent_name=request.agent_name,
            room=request.room,
            metadata=request.metadata,
            state=1,
        )


@pytest.mark.anyio
async def test_livekit_adapter_uses_pinned_list_and_create_contract() -> None:
    dispatch_service = _DispatchService()
    api = SimpleNamespace(agent_dispatch=dispatch_service)
    telemetry = _Telemetry()
    provider = LiveKitDispatchAPIProvider(livekit_api=api, observability=telemetry)

    listed = await provider.list_dispatches(room_name="room-1")
    created = await provider.create_dispatch(
        agent_name="Ava",
        room_name="room-1",
        metadata='{"call_id":"call-1"}',
    )

    assert dispatch_service.list_rooms == ["room-1"]
    assert [(item.id, item.agent_name, item.room) for item in listed] == [
        ("dispatch-existing", "Ava", "room-1")
    ]
    request = dispatch_service.create_requests[0]
    assert request.agent_name == "Ava"
    assert request.room == "room-1"
    assert request.metadata == '{"call_id":"call-1"}'
    assert created.id == "dispatch-created"
    assert telemetry.calls == [
        ("livekit", "list_dispatches", "success"),
        ("livekit", "create_dispatch", "success"),
    ]


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("id", None),
        ("id", ""),
        ("agent_name", None),
        ("agent_name", "   "),
        ("room", None),
        ("room", ""),
        ("metadata", None),
        ("metadata", {"call_id": "not-a-provider-string"}),
    ],
)
def test_livekit_adapter_rejects_malformed_provider_dispatch_fields(
    field_name: str,
    invalid_value,
) -> None:
    response = {
        "id": "dispatch-valid",
        "agent_name": "configured-worker",
        "room": "room-valid",
        "metadata": '{"call_id":"call-valid"}',
        "state": 1,
    }
    response[field_name] = invalid_value

    with pytest.raises(ValueError, match="Invalid LiveKit dispatch response"):
        LiveKitDispatchAPIProvider._to_dispatch(SimpleNamespace(**response))
