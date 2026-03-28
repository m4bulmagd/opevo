from types import SimpleNamespace

import pytest

from app.providers.livekit_recording.livekit import (
    LiveKitRecordingProvider,
    LiveKitRecordingProviderError,
)


class FakeEgressClient:
    def __init__(self) -> None:
        self.requests: list[object] = []

    async def start_room_composite_egress(self, request) -> object:
        self.requests.append(request)
        return SimpleNamespace(egress_id="egress_123")


@pytest.mark.anyio
async def test_start_room_recording_uses_audio_only_room_composite() -> None:
    egress_client = FakeEgressClient()
    provider = LiveKitRecordingProvider(
        egress_client=egress_client,
        bucket_name="recordings",
        endpoint_url="http://minio:9000",
        access_key="minioadmin",
        secret_key="minioadmin",
        region="us-east-1",
    )

    result = await provider.start_room_recording(
        room_name="room_123",
        user_id="user_123",
        call_id="call_456",
    )

    request = egress_client.requests[0]
    assert request.room_name == "room_123"
    assert request.audio_only is True
    assert request.file.filepath == "calls/user_123/call_456.ogg"
    assert request.file.s3.bucket == "recordings"
    assert request.file.s3.endpoint == "http://minio:9000"
    assert request.file.s3.access_key == "minioadmin"
    assert request.file.s3.secret == "minioadmin"
    assert request.file.s3.region == "us-east-1"
    assert request.file.s3.force_path_style is True
    assert result.egress_id == "egress_123"
    assert result.object_key == "calls/user_123/call_456.ogg"
    assert result.url == "http://minio:9000/recordings/calls/user_123/call_456.ogg"


@pytest.mark.anyio
async def test_start_room_recording_wraps_provider_failures() -> None:
    class FailingEgressClient:
        async def start_room_composite_egress(self, request) -> object:
            raise RuntimeError("egress unavailable")

    provider = LiveKitRecordingProvider(
        egress_client=FailingEgressClient(),
        bucket_name="recordings",
        endpoint_url="http://minio:9000",
        access_key="minioadmin",
        secret_key="minioadmin",
        region="us-east-1",
    )

    with pytest.raises(LiveKitRecordingProviderError, match="egress unavailable"):
        await provider.start_room_recording(
            room_name="room_123",
            user_id="user_123",
            call_id="call_456",
        )
