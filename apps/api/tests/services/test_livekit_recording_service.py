from types import SimpleNamespace
import pytest
from app.services.livekit_recording_service import LiveKitRecordingService


class RecordingProviderSpy:
    def __init__(self, *, failure: Exception | None = None) -> None:
        self.failure = failure
        self.calls: list[tuple[str, ...]] = []

    async def start_room_recording(
        self,
        *,
        room_name: str,
        object_key: str,
    ) -> object:
        self.calls.append(("start_room_recording", room_name, object_key))
        if self.failure is not None:
            raise self.failure
        return SimpleNamespace(
            egress_id="egress-1",
            object_key=object_key,
            url=None,
        )

    async def list_room_egresses(self, *, room_name: str) -> tuple[object, ...]:
        self.calls.append(("list_room_egresses", room_name))
        if self.failure is not None:
            raise self.failure
        return (SimpleNamespace(egress_id="egress-1"),)

    async def ensure_stopped(self, egress_id: str) -> None:
        self.calls.append(("ensure_stopped", egress_id))
        if self.failure is not None:
            raise self.failure

    async def ensure_not_running(self, egress_id: str) -> None:
        self.calls.append(("ensure_not_running", egress_id))
        if self.failure is not None:
            raise self.failure


@pytest.mark.anyio
async def test_start_forwards_committed_object_key_exactly_to_provider() -> None:
    provider = RecordingProviderSpy()
    expected_object_key = "calls/committed-owner/committed-call.ogg"

    result = await LiveKitRecordingService(provider=provider).start_room_recording(
        room_name="room-owned",
        object_key=expected_object_key,
    )

    assert provider.calls == [
        (
            "start_room_recording",
            "room-owned",
            expected_object_key,
        )
    ]
    assert result.object_key == expected_object_key


@pytest.mark.anyio
async def test_list_room_egresses_delegates_to_injected_provider() -> None:
    provider = RecordingProviderSpy()

    result = await LiveKitRecordingService(provider=provider).list_room_egresses(
        room_name="room-owned"
    )

    assert provider.calls == [("list_room_egresses", "room-owned")]
    assert result[0].egress_id == "egress-1"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "operation",
    ["ensure_stopped", "ensure_not_running"],
)
async def test_ensure_operations_delegate_to_injected_provider(
    operation: str,
) -> None:
    provider = RecordingProviderSpy()

    await getattr(LiveKitRecordingService(provider=provider), operation)("egress-1")

    assert provider.calls == [(operation, "egress-1")]
