from types import SimpleNamespace

import pytest

from app.providers.livekit_recording.livekit import (
    LiveKitRecordingProvider,
    LiveKitRecordingProviderError,
)


class FakeEgressClient:
    def __init__(self) -> None:
        self.requests: list[object] = []
        self.stop_requests: list[str] = []

    async def start_room_composite_egress(self, request) -> object:
        self.requests.append(request)
        return SimpleNamespace(egress_id="egress_123")

    async def stop_egress(self, request) -> object:
        self.stop_requests.append(request.egress_id)
        return SimpleNamespace()


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


@pytest.mark.anyio
async def test_start_room_recording_uses_aws_native_s3_shape() -> None:
    egress_client = FakeEgressClient()
    provider = LiveKitRecordingProvider(
        egress_client=egress_client,
        bucket_name="recordings",
        endpoint_url="https://s3.eu-west-3.amazonaws.com",
        access_key="aws-access",
        secret_key="aws-secret",
        region="eu-west-3",
    )

    await provider.start_room_recording(
        room_name="room_123",
        user_id="user_123",
        call_id="call_456",
    )

    request = egress_client.requests[0]
    assert request.file.s3.bucket == "recordings"
    assert request.file.s3.region == "eu-west-3"
    assert request.file.s3.access_key == "aws-access"
    assert request.file.s3.secret == "aws-secret"
    assert request.file.s3.force_path_style is False
    assert request.file.s3.endpoint == ""


@pytest.mark.anyio
async def test_stop_room_recording_stops_egress() -> None:
    egress_client = FakeEgressClient()
    provider = LiveKitRecordingProvider(
        egress_client=egress_client,
        bucket_name="recordings",
        endpoint_url="http://minio:9000",
        access_key="minioadmin",
        secret_key="minioadmin",
        region="us-east-1",
    )

    await provider.stop_room_recording(egress_id="egress_123")

    assert egress_client.stop_requests == ["egress_123"]
