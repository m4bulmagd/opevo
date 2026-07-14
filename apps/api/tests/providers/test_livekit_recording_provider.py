from types import SimpleNamespace
from contextlib import asynccontextmanager

import pytest
from livekit import api

from app.providers.livekit_recording.livekit import (
    LiveKitRecordingProvider,
    LiveKitRecordingProviderError,
)


class _Telemetry:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []
        self.error_classes: list[str] = []

    @asynccontextmanager
    async def provider_operation(self, provider: str, operation: str, **_kwargs):
        try:
            yield
        except Exception as error:
            from app.core.observability import normalize_error_class

            self.calls.append((provider, operation, "error"))
            self.error_classes.append(normalize_error_class(error))
            raise
        else:
            self.calls.append((provider, operation, "success"))


class FakeEgressClient:
    def __init__(self, status_sequences: list[list[int]]) -> None:
        self.status_sequences = list(status_sequences)
        self.list_requests = []
        self.stop_requests = []

    async def list_egress(self, request):
        self.list_requests.append(request)
        statuses = self.status_sequences.pop(0)
        return SimpleNamespace(
            items=[
                SimpleNamespace(egress_id="egress-1", status=status)
                for status in statuses
            ]
        )

    async def stop_egress(self, request):
        self.stop_requests.append(request)


class FakeStartEgressClient(FakeEgressClient):
    def __init__(self, *, failure: Exception | None = None) -> None:
        super().__init__([])
        self.failure = failure
        self.start_requests = []

    async def start_room_composite_egress(self, request):
        self.start_requests.append(request)
        if self.failure is not None:
            raise self.failure
        return SimpleNamespace(egress_id="egress-started")


def build_provider(client: FakeEgressClient) -> LiveKitRecordingProvider:
    return LiveKitRecordingProvider(
        egress_client=client,
        bucket_name="recordings",
        endpoint_url="http://minio:9000",
        access_key="key",
        secret_key="secret",
        region="us-east-1",
    )


async def ensure_stopped(provider: LiveKitRecordingProvider, egress_id: str) -> None:
    method = getattr(provider, "ensure_stopped", None)
    assert method is not None, "recording provider must expose ensure_stopped"
    await method(egress_id)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "terminal_status",
    [
        api.EgressStatus.EGRESS_COMPLETE,
    ],
)
async def test_ensure_stopped_accepts_only_completed_terminal_egress(
    terminal_status: int,
) -> None:
    terminal = FakeEgressClient([[terminal_status]])

    await ensure_stopped(build_provider(terminal), "egress-1")

    assert terminal.stop_requests == []


@pytest.mark.anyio
async def test_ensure_stopped_retries_when_initial_egress_lookup_is_missing() -> None:
    client = FakeEgressClient([[]])
    telemetry = _Telemetry()
    provider = LiveKitRecordingProvider(
        egress_client=client,
        bucket_name="recordings",
        endpoint_url="http://minio:9000",
        access_key="key",
        secret_key="secret",
        region="us-east-1",
        observability=telemetry,
    )

    with pytest.raises(LiveKitRecordingProviderError) as exc_info:
        await ensure_stopped(provider, "egress-1")

    assert exc_info.value.category == "provider_retryable"
    assert exc_info.value.retryable is True
    assert exc_info.value.error_class == "unavailable"
    assert str(exc_info.value) == "provider_retryable"
    assert client.stop_requests == []
    assert telemetry.calls == [
        ("livekit", "ensure_recording_stopped", "error")
    ]
    assert telemetry.error_classes == ["unavailable"]


@pytest.mark.anyio
async def test_ensure_stopped_retries_when_post_stop_egress_lookup_is_missing() -> None:
    client = FakeEgressClient([[api.EgressStatus.EGRESS_ACTIVE], []])

    with pytest.raises(LiveKitRecordingProviderError) as exc_info:
        await ensure_stopped(build_provider(client), "egress-1")

    assert exc_info.value.category == "provider_retryable"
    assert exc_info.value.retryable is True
    assert exc_info.value.error_class == "unavailable"
    assert str(exc_info.value) == "provider_retryable"
    assert len(client.stop_requests) == 1
    assert len(client.list_requests) == 2


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("failed_status", "expected_error_class"),
    [
        (api.EgressStatus.EGRESS_FAILED, "unknown"),
        (api.EgressStatus.EGRESS_ABORTED, "conflict"),
        (api.EgressStatus.EGRESS_LIMIT_REACHED, "rate_limited"),
    ],
)
async def test_ensure_stopped_reports_failed_terminal_egress_as_provider_error(
    failed_status: int,
    expected_error_class: str,
) -> None:
    client = FakeEgressClient([[failed_status]])
    telemetry = _Telemetry()
    provider = LiveKitRecordingProvider(
        egress_client=client,
        bucket_name="recordings",
        endpoint_url="http://minio:9000",
        access_key="key",
        secret_key="secret",
        region="us-east-1",
        observability=telemetry,
    )

    with pytest.raises(LiveKitRecordingProviderError) as exc_info:
        await ensure_stopped(provider, "egress-1")

    assert exc_info.value.category == "provider_terminal"
    assert exc_info.value.retryable is False
    assert exc_info.value.error_class == expected_error_class
    assert str(exc_info.value) == "provider_terminal"
    assert client.stop_requests == []
    assert telemetry.calls == [
        ("livekit", "ensure_recording_stopped", "error")
    ]
    assert telemetry.error_classes == [expected_error_class]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "active_status",
    [
        api.EgressStatus.EGRESS_STARTING,
        api.EgressStatus.EGRESS_ACTIVE,
        api.EgressStatus.EGRESS_ENDING,
    ],
)
async def test_ensure_stopped_stops_and_rechecks_active_egress(
    active_status: int,
) -> None:
    client = FakeEgressClient(
        [[active_status], [api.EgressStatus.EGRESS_COMPLETE]]
    )

    await ensure_stopped(build_provider(client), "egress-1")

    assert len(client.stop_requests) == 1
    assert client.stop_requests[0].egress_id == "egress-1"
    assert len(client.list_requests) == 2


@pytest.mark.anyio
async def test_ensure_stopped_retries_when_recheck_is_still_active() -> None:
    client = FakeEgressClient(
        [
            [api.EgressStatus.EGRESS_ACTIVE],
            [api.EgressStatus.EGRESS_ENDING],
        ]
    )

    with pytest.raises(LiveKitRecordingProviderError) as exc_info:
        await ensure_stopped(build_provider(client), "egress-1")

    assert exc_info.value.category == "provider_retryable"
    assert exc_info.value.retryable is True
    assert exc_info.value.error_class == "unavailable"
    assert str(exc_info.value) == "provider_retryable"


@pytest.mark.anyio
async def test_start_room_recording_uses_audio_only_direct_minio_output() -> None:
    client = FakeStartEgressClient()
    telemetry = _Telemetry()
    provider = LiveKitRecordingProvider(
        egress_client=client,
        bucket_name="recordings",
        endpoint_url="http://minio:9000/",
        access_key="minio-key",
        secret_key="minio-secret",
        region="us-east-1",
        observability=telemetry,
    )

    result = await provider.start_room_recording(
        room_name="room-1",
        user_id="user-1",
        call_id="call-1",
    )

    assert result.egress_id == "egress-started"
    assert result.object_key == "calls/user-1/call-1.ogg"
    request = client.start_requests[0]
    assert request.room_name == "room-1"
    assert request.audio_only is True
    assert request.file.filepath == "calls/user-1/call-1.ogg"
    assert request.file.s3.bucket == "recordings"
    assert request.file.s3.endpoint == "http://minio:9000"
    assert request.file.s3.access_key == "minio-key"
    assert request.file.s3.secret == "minio-secret"
    assert request.file.s3.region == "us-east-1"
    assert request.file.s3.force_path_style is True
    assert telemetry.calls == [("livekit", "start_recording", "success")]


@pytest.mark.anyio
async def test_start_room_recording_uses_aws_native_s3_shape() -> None:
    client = FakeStartEgressClient()
    provider = LiveKitRecordingProvider(
        egress_client=client,
        bucket_name="recordings",
        endpoint_url="https://s3.eu-west-3.amazonaws.com",
        access_key="aws-key",
        secret_key="aws-secret",
        region="eu-west-3",
    )

    await provider.start_room_recording(
        room_name="room-aws",
        user_id="user-aws",
        call_id="call-aws",
    )

    upload = client.start_requests[0].file.s3
    assert upload.bucket == "recordings"
    assert upload.region == "eu-west-3"
    assert upload.force_path_style is False
    assert upload.endpoint == ""


@pytest.mark.anyio
async def test_start_room_recording_wraps_provider_failures() -> None:
    client = FakeStartEgressClient(
        failure=RuntimeError("provider unavailable SECRET_PROVIDER_MESSAGE")
    )

    with pytest.raises(LiveKitRecordingProviderError) as exc_info:
        await build_provider(client).start_room_recording(
            room_name="room-1",
            user_id="user-1",
            call_id="call-1",
        )

    assert exc_info.value.category == "provider_retryable"
    assert exc_info.value.retryable is True
    assert exc_info.value.error_class == "unknown"
    assert str(exc_info.value) == "provider_retryable"
    assert exc_info.value.__cause__ is None
