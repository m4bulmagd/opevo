from types import SimpleNamespace
from contextlib import asynccontextmanager

import pytest
from livekit import api

from app.core.provider_failures import ProviderFailure
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


_TWIRP_FAILURE_CASES = (
    ("deadline_exceeded", "retryable", "timeout"),
    ("resource_exhausted", "retryable", "rate_limited"),
    ("unavailable", "retryable", "unavailable"),
    ("internal", "retryable", "unavailable"),
    ("unauthenticated", "terminal", "authentication"),
    ("permission_denied", "terminal", "authentication"),
    ("already_exists", "terminal", "conflict"),
    ("aborted", "terminal", "conflict"),
    ("not_found", "terminal", "not_found"),
    ("invalid_argument", "terminal", "validation"),
    ("malformed", "terminal", "validation"),
    ("failed_precondition", "terminal", "validation"),
    ("out_of_range", "terminal", "validation"),
    ("bad_route", "terminal", "validation"),
    ("unimplemented", "terminal", "validation"),
    ("unknown", "retryable", "unknown"),
    ("canceled", "terminal", "unknown"),
    ("dataloss", "terminal", "unknown"),
)


@pytest.mark.anyio
@pytest.mark.parametrize(("code", "disposition", "error_class"), _TWIRP_FAILURE_CASES)
@pytest.mark.parametrize("operation", ["list", "create"])
async def test_livekit_dispatch_adapter_maps_every_twirp_code_to_safe_provider_failure(
    code: str,
    disposition: str,
    error_class: str,
    operation: str,
) -> None:
    cause = api.TwirpError(code, "provider detail must not escape", status=418)

    class FailingDispatchService:
        async def list_dispatch(self, _room_name: str):
            raise cause

        async def create_dispatch(self, _request: object):
            raise cause

    provider = LiveKitDispatchAPIProvider(
        livekit_api=SimpleNamespace(agent_dispatch=FailingDispatchService()),
        observability=_Telemetry(),
    )
    if operation == "list":

        async def invoke() -> object:
            return await provider.list_dispatches(room_name="room-owned")

        expected_operation = "list_dispatches"
    else:

        async def invoke() -> object:
            return await provider.create_dispatch(
                agent_name="Ava",
                room_name="room-owned",
                metadata='{"call_id":"call-owned"}',
            )

        expected_operation = "create_dispatch"

    with pytest.raises(ProviderFailure) as exc_info:
        await invoke()

    failure = exc_info.value
    assert (
        failure.provider,
        failure.operation,
        failure.disposition,
        failure.error_class,
        failure.context,
    ) == ("livekit", expected_operation, disposition, error_class, {})
    assert failure.__cause__ is cause
    assert "provider detail must not escape" not in str(failure)


@pytest.mark.anyio
async def test_livekit_dispatch_malformed_response_is_terminal_validation_and_defects_escape() -> None:
    class MalformedDispatchService:
        async def list_dispatch(self, _room_name: str):
            return [SimpleNamespace(id="dispatch", agent_name="Ava", room="", metadata="{}")]

    provider = LiveKitDispatchAPIProvider(
        livekit_api=SimpleNamespace(agent_dispatch=MalformedDispatchService()),
        observability=_Telemetry(),
    )
    with pytest.raises(ProviderFailure) as exc_info:
        await provider.list_dispatches(room_name="room-owned")
    assert (exc_info.value.disposition, exc_info.value.error_class) == (
        "terminal",
        "validation",
    )

    defect = TypeError("INTERNAL_SENTINEL")

    class DefectiveDispatchService:
        async def list_dispatch(self, _room_name: str):
            raise defect

    with pytest.raises(TypeError) as defect_info:
        await LiveKitDispatchAPIProvider(
            livekit_api=SimpleNamespace(agent_dispatch=DefectiveDispatchService()),
            observability=_Telemetry(),
        ).list_dispatches(room_name="room-owned")
    assert defect_info.value is defect


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


@pytest.mark.anyio
async def test_livekit_adapter_accepts_unnamed_automatic_dispatches() -> None:
    dispatch_service = _DispatchService()

    async def list_dispatch(room_name: str):
        return [
            SimpleNamespace(
                id="dispatch-automatic",
                agent_name="",
                room=room_name,
                metadata="",
                state=1,
            )
        ]

    dispatch_service.list_dispatch = list_dispatch
    provider = LiveKitDispatchAPIProvider(
        livekit_api=SimpleNamespace(agent_dispatch=dispatch_service),
        observability=_Telemetry(),
    )

    listed = await provider.list_dispatches(room_name="room-automatic")

    assert len(listed) == 1
    assert listed[0].agent_name == ""


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("id", None),
        ("id", ""),
        ("agent_name", None),
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
