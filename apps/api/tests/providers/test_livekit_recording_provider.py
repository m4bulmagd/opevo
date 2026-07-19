from contextlib import asynccontextmanager
from collections.abc import Iterator
from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest
from livekit import api

from app.providers.livekit_recording import base as recording_base
from app.providers.livekit_recording.livekit import (
    LiveKitRecordingProvider,
    LiveKitRecordingProviderError,
    normalized_egress_object_key_evidence,
)


class _Telemetry:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []
        self.error_classes: list[str] = []
        self.operation_kwargs: list[dict[str, object]] = []

    @asynccontextmanager
    async def provider_operation(self, provider: str, operation: str, **kwargs):
        self.operation_kwargs.append(dict(kwargs))
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


_DEFAULT_START_RESULT = object()


class FakeStartEgressClient(FakeEgressClient):
    def __init__(
        self,
        *,
        failure: Exception | None = None,
        result: object = _DEFAULT_START_RESULT,
    ) -> None:
        super().__init__([])
        self.failure = failure
        self.result = result
        self.start_requests = []

    async def start_room_composite_egress(self, request):
        self.start_requests.append(request)
        if self.failure is not None:
            raise self.failure
        if self.result is _DEFAULT_START_RESULT:
            return SimpleNamespace(egress_id="egress-started")
        return self.result


class FakeRoomListEgressClient:
    def __init__(
        self,
        items: list[object],
        *,
        failure: Exception | None = None,
    ) -> None:
        self.items = items
        self.failure = failure
        self.list_requests = []

    async def list_egress(self, request):
        self.list_requests.append(request)
        if self.failure is not None:
            raise self.failure
        return SimpleNamespace(items=self.items)


class _EquivalentPath(str):
    pass


class _RepeatedIteratorConstructionFailure:
    def __iter__(self) -> Iterator[object]:
        raise RuntimeError("repeated iterator unavailable")


class _RepeatedIterationFailure:
    def __iter__(self) -> Iterator[object]:
        yield {"filename": "calls/user-1/call-1.ogg"}
        raise RuntimeError("repeated iteration failed")


class _InfiniteLikeRepeated:
    def __init__(self) -> None:
        self.inspections = 0

    def __iter__(self) -> Iterator[object]:
        return self

    def __next__(self) -> object:
        self.inspections += 1
        if self.inspections > 65:
            raise RuntimeError("repeated input consumed past sentinel")
        return {"filename": "calls/user-1/call-1.ogg"}


class _ProviderAbort(BaseException):
    pass


class _SdkRecordAccessFailure:
    def __init__(self, *, failure_type: type[BaseException] = RuntimeError) -> None:
        self.room_name = "room-owned"
        self.status = int(api.EgressStatus.EGRESS_ACTIVE)
        self.failure_type = failure_type

    @property
    def egress_id(self) -> str:
        raise self.failure_type("SDK identity attribute unavailable")


class _ProviderDescriptorAccessFailure:
    egress_id = "EG_exact"
    room_name = "room-owned"
    status = int(api.EgressStatus.EGRESS_ACTIVE)

    @property
    def DESCRIPTOR(self) -> object:
        raise RuntimeError("protobuf descriptor unavailable")


class _ProviderPresenceProbeFailure:
    DESCRIPTOR = SimpleNamespace(
        fields_by_name={
            "egress_id": SimpleNamespace(has_presence=True),
        }
    )
    egress_id = "EG_exact"
    room_name = "room-owned"
    status = int(api.EgressStatus.EGRESS_ACTIVE)

    def __init__(self, *, failure_type: type[BaseException] = RuntimeError) -> None:
        self.failure_type = failure_type

    def HasField(self, _name: str) -> bool:
        raise self.failure_type("protobuf presence unavailable")


class _ProviderProtocolClassificationFailure:
    def __init__(
        self,
        *,
        failure_type: type[BaseException] = RuntimeError,
    ) -> None:
        self.failure_type = failure_type

    @property
    def __class__(self) -> type:
        raise self.failure_type("protocol classification unavailable")


def twirp_error(*, code: str, status: int) -> api.TwirpError:
    return api.TwirpError(code, "provider detail must not escape", status=status)


def build_provider(
    client: FakeEgressClient,
    *,
    observability=None,
    endpoint_url: str = "http://minio:9000",
) -> LiveKitRecordingProvider:
    return LiveKitRecordingProvider(
        egress_client=client,
        bucket_name="recordings",
        endpoint_url=endpoint_url,
        access_key="key",
        secret_key="secret",
        region="us-east-1",
        observability=observability,
    )


async def ensure_stopped(provider: LiveKitRecordingProvider, egress_id: str) -> None:
    method = getattr(provider, "ensure_stopped", None)
    assert method is not None, "recording provider must expose ensure_stopped"
    await method(egress_id)


async def ensure_not_running(
    provider: LiveKitRecordingProvider,
    egress_id: str,
) -> None:
    method = getattr(provider, "ensure_not_running", None)
    assert method is not None, "recording provider must expose ensure_not_running"
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
    assert telemetry.calls == [("livekit", "ensure_recording_stopped", "error")]
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
    assert telemetry.calls == [("livekit", "ensure_recording_stopped", "error")]
    assert telemetry.error_classes == [expected_error_class]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "failed_status",
    [
        api.EgressStatus.EGRESS_FAILED,
        api.EgressStatus.EGRESS_ABORTED,
        api.EgressStatus.EGRESS_LIMIT_REACHED,
    ],
)
async def test_ensure_not_running_accepts_failed_terminal_egress_while_ensure_stopped_rejects_it(
    failed_status: int,
) -> None:
    stop_job_client = FakeEgressClient([[failed_status]])
    deletion_client = FakeEgressClient([[failed_status]])
    telemetry = _Telemetry()

    with pytest.raises(LiveKitRecordingProviderError) as exc_info:
        await ensure_stopped(build_provider(stop_job_client), "egress-1")

    await ensure_not_running(
        build_provider(deletion_client, observability=telemetry),
        "egress-1",
    )

    assert exc_info.value.category == "provider_terminal"
    assert stop_job_client.stop_requests == []
    assert deletion_client.stop_requests == []
    assert telemetry.calls == [("livekit", "ensure_recording_not_running", "success")]


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
    client = FakeEgressClient([[active_status], [api.EgressStatus.EGRESS_COMPLETE]])

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
        object_key="calls/user-1/call-1.ogg",
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
        object_key="calls/user-aws/call-aws.ogg",
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
            object_key="calls/user-1/call-1.ogg",
        )

    assert exc_info.value.category == "provider_retryable"
    assert exc_info.value.retryable is True
    assert exc_info.value.error_class == "unknown"
    assert exc_info.value.start_outcome == "unknown"
    assert str(exc_info.value) == "provider_retryable"
    assert exc_info.value.__cause__ is None


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (twirp_error(code="invalid_argument", status=400), "not_started"),
        (TimeoutError(), "unknown"),
        (ValueError("unexpected provider failure"), "unknown"),
    ],
)
async def test_start_room_recording_exposes_classified_outcome(
    failure: Exception,
    expected: str,
) -> None:
    client = FakeStartEgressClient(failure=failure)

    with pytest.raises(LiveKitRecordingProviderError) as exc_info:
        await build_provider(client).start_room_recording(
            room_name="room-1",
            object_key="calls/user-1/call-1.ogg",
        )

    assert exc_info.value.start_outcome == expected


@pytest.mark.anyio
async def test_start_room_recording_rejects_empty_object_key_before_io() -> None:
    client = FakeStartEgressClient()

    with pytest.raises(LiveKitRecordingProviderError) as exc_info:
        await build_provider(client).start_room_recording(
            room_name="room-1",
            object_key="",
        )

    assert exc_info.value.category == "provider_terminal"
    assert exc_info.value.error_class == "validation"
    assert exc_info.value.start_outcome == "not_started"
    assert client.start_requests == []


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (ValueError("unexpected provider failure"), "unknown"),
        (twirp_error(code="invalid_argument", status=400), "not_started"),
        (twirp_error(code="unauthenticated", status=401), "not_started"),
        (twirp_error(code="permission_denied", status=403), "not_started"),
        (TimeoutError(), "unknown"),
        (ConnectionError(), "unknown"),
        (twirp_error(code="already_exists", status=409), "unknown"),
        (twirp_error(code="resource_exhausted", status=429), "unknown"),
        (twirp_error(code="internal", status=500), "unknown"),
        (RuntimeError("unexpected"), "unknown"),
    ],
)
def test_start_outcome_classification(
    error: Exception,
    expected: str,
) -> None:
    assert LiveKitRecordingProvider.start_outcome_for(error) == expected


def test_recording_provider_error_start_outcome_is_immutable() -> None:
    error = LiveKitRecordingProviderError(
        "provider_terminal",
        error_class="validation",
        start_outcome="not_started",
    )

    with pytest.raises(AttributeError):
        error.start_outcome = "unknown"

    assert error.start_outcome == "not_started"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "result",
    [
        None,
        SimpleNamespace(),
        SimpleNamespace(egress_id=""),
        SimpleNamespace(egress_id="   "),
        SimpleNamespace(egress_id="bad\x00id"),
        SimpleNamespace(egress_id="E" * 256),
        SimpleNamespace(egress_id=object()),
    ],
)
async def test_start_room_recording_treats_malformed_result_as_unknown(
    result: object,
) -> None:
    client = FakeStartEgressClient(result=result)

    with pytest.raises(LiveKitRecordingProviderError) as exc_info:
        await build_provider(client).start_room_recording(
            room_name="room-1",
            object_key="calls/user-1/call-1.ogg",
        )

    assert exc_info.value.category == "provider_retryable"
    assert exc_info.value.error_class == "unknown"
    assert exc_info.value.start_outcome == "unknown"
    assert str(exc_info.value) == "provider_retryable"


@pytest.mark.anyio
async def test_list_room_egresses_returns_sanitized_primitive_snapshots() -> None:
    object_key = "calls/user-1/call-1.ogg"
    client = FakeRoomListEgressClient(
        [
            api.EgressInfo(
                egress_id="egress-room-composite",
                room_name="room-owned",
                status=api.EgressStatus.EGRESS_ACTIVE,
                room_composite=api.RoomCompositeEgressRequest(
                    file=api.EncodedFileOutput(filepath=object_key)
                ),
            ),
            api.EgressInfo(
                egress_id="egress-file-result",
                room_name="room-owned",
                status=api.EgressStatus.EGRESS_COMPLETE,
                file_results=[api.FileInfo(filename=object_key)],
            ),
        ]
    )

    snapshots = await build_provider(client).list_room_egresses(room_name="room-owned")
    assert snapshots == (
        recording_base.RecordingEgressSnapshot(
            egress_id="egress-room-composite",
            room_name="room-owned",
            status=int(api.EgressStatus.EGRESS_ACTIVE),
            object_key=object_key,
        ),
        recording_base.RecordingEgressSnapshot(
            egress_id="egress-file-result",
            room_name="room-owned",
            status=int(api.EgressStatus.EGRESS_COMPLETE),
            object_key=object_key,
        ),
    )
    assert client.list_requests[0].room_name == "room-owned"
    assert all(type(snapshot.egress_id) is str for snapshot in snapshots)
    assert all(type(snapshot.room_name) is str for snapshot in snapshots)
    assert all(type(snapshot.status) is int for snapshot in snapshots)
    assert all(
        snapshot.object_key is None or type(snapshot.object_key) is str
        for snapshot in snapshots
    )
    assert all(not isinstance(snapshot, api.EgressInfo) for snapshot in snapshots)

    with pytest.raises(FrozenInstanceError):
        snapshots[0].object_key = "provider-controlled-change"


@pytest.mark.anyio
async def test_list_room_egresses_is_instrumented_without_room_identity() -> None:
    room_sentinel = "ROOM_PRIVATE_LIST_OPERATION_SENTINEL"
    telemetry = _Telemetry()
    client = FakeRoomListEgressClient([])

    snapshots = await build_provider(
        client,
        observability=telemetry,
    ).list_room_egresses(room_name=room_sentinel)

    assert snapshots == ()
    assert client.list_requests[0].room_name == room_sentinel
    assert telemetry.calls == [
        ("livekit", "list_recording_egresses", "success")
    ]
    assert telemetry.operation_kwargs == [{"call_id": None}]
    assert room_sentinel not in repr(
        (telemetry.calls, telemetry.operation_kwargs)
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "item",
    [
        {
            "egressId": "egress-mapping",
            "roomName": "room-owned",
            "status": int(api.EgressStatus.EGRESS_ACTIVE),
            "roomComposite": {"fileOutputs": [{"filepath": "calls/user-1/call-1.ogg"}]},
        },
        {
            "egress_id": "egress-mapping",
            "room_name": "room-owned",
            "status": int(api.EgressStatus.EGRESS_ACTIVE),
            "file_results": [{"filename": "calls/user-1/call-1.ogg"}],
        },
    ],
)
async def test_list_room_egresses_normalizes_mapping_aliases(item: dict) -> None:
    client = FakeRoomListEgressClient([item])

    snapshots = await build_provider(client).list_room_egresses(room_name="room-owned")
    assert snapshots == (
        recording_base.RecordingEgressSnapshot(
            egress_id="egress-mapping",
            room_name="room-owned",
            status=int(api.EgressStatus.EGRESS_ACTIVE),
            object_key="calls/user-1/call-1.ogg",
        ),
    )


@pytest.mark.anyio
async def test_list_room_egresses_accepts_semantically_equivalent_path_aliases() -> None:
    object_key = "calls/user-1/call-1.ogg"
    item = {
        "egressId": "egress-mapping",
        "egress_id": "egress-mapping",
        "roomName": "room-owned",
        "room_name": "room-owned",
        "status": int(api.EgressStatus.EGRESS_ACTIVE),
        "roomComposite": {
            "fileOutputs": [{"filepath": object_key}],
        },
        "room_composite": {
            "file_outputs": [{"filepath": object_key}],
        },
    }

    snapshots = await build_provider(
        FakeRoomListEgressClient([item])
    ).list_room_egresses(room_name="room-owned")

    assert snapshots[0].object_key == object_key
    evidence = normalized_egress_object_key_evidence(
        item,
        bucket_name="recordings",
        endpoint_url="http://minio:9000",
    )
    assert evidence.state == "exact"
    assert evidence.object_key == object_key


@pytest.mark.anyio
async def test_list_room_egresses_rejects_equal_path_leaves_of_different_types() -> None:
    object_key = "calls/user-1/call-1.ogg"
    item = {
        "egressId": "egress-mapping",
        "roomName": "room-owned",
        "status": int(api.EgressStatus.EGRESS_ACTIVE),
        "roomComposite": {
            "fileOutputs": [{"filepath": _EquivalentPath(object_key)}],
        },
        "room_composite": {
            "file_outputs": ({"filepath": object_key},),
        },
    }

    snapshots = await build_provider(
        FakeRoomListEgressClient([item])
    ).list_room_egresses(room_name="room-owned")
    evidence = normalized_egress_object_key_evidence(
        item,
        bucket_name="recordings",
        endpoint_url="http://minio:9000",
    )

    assert snapshots[0].object_key is None
    assert evidence.state == "invalid"
    assert evidence.object_key is None


@pytest.mark.anyio
@pytest.mark.parametrize(
    "path_shape",
    [
        {"roomComposite": "not-an-object"},
        {"roomComposite": {"file": "not-an-object"}},
        {"roomComposite": {"file": {"filepath": None}}},
        {"fileResults": [None]},
        {"fileResults": ["not-an-object"]},
        {
            "room_composite": {"file": {"filepath": "calls/user-1/call-1.ogg"}},
            "roomComposite": {"file": {"filepath": "calls/user-1/other.ogg"}},
        },
        {"fileResults": [{"filename": "calls/user-1/call-1.ogg\x00suffix"}]},
        {"fileResults": [{"filename": "   "}]},
    ],
)
async def test_list_room_egresses_marks_malformed_mapping_paths_untrusted(
    path_shape: dict,
) -> None:
    item = {
        "egressId": "egress-mapping",
        "roomName": "room-owned",
        "status": int(api.EgressStatus.EGRESS_ACTIVE),
        **path_shape,
    }

    snapshots = await build_provider(
        FakeRoomListEgressClient([item])
    ).list_room_egresses(room_name="room-owned")

    assert snapshots[0].object_key is None
    evidence = normalized_egress_object_key_evidence(
        item,
        bucket_name="recordings",
        endpoint_url="http://minio:9000",
    )
    assert evidence.state == "invalid"


@pytest.mark.parametrize(
    "repeated",
    [_RepeatedIteratorConstructionFailure(), _RepeatedIterationFailure()],
)
@pytest.mark.parametrize("field_name", ["fileResults", "fileOutputs"])
def test_object_key_evidence_contains_malformed_repeated_iteration(
    field_name: str,
    repeated: object,
) -> None:
    egress = (
        {"fileResults": repeated}
        if field_name == "fileResults"
        else {"roomComposite": {"fileOutputs": repeated}}
    )

    evidence = normalized_egress_object_key_evidence(
        egress,
        bucket_name="recordings",
        endpoint_url="http://minio:9000",
    )

    assert evidence.state == "invalid"
    assert evidence.object_key is None


def test_object_key_evidence_bounds_infinite_like_repeated_input() -> None:
    repeated = _InfiniteLikeRepeated()

    evidence = normalized_egress_object_key_evidence(
        {"fileResults": repeated},
        bucket_name="recordings",
        endpoint_url="http://minio:9000",
    )

    assert evidence.state == "invalid"
    assert evidence.object_key is None
    assert repeated.inspections == 65


@pytest.mark.anyio
@pytest.mark.parametrize(
    "item",
    [
        {"egressId": "   ", "roomName": "room", "status": 1},
        {"egressId": "bad\x00id", "roomName": "room", "status": 1},
        {"egressId": "E" * 256, "roomName": "room", "status": 1},
        {"egressId": "EG", "roomName": "bad\x00room", "status": 1},
        {"egressId": "EG", "roomName": "   ", "status": 1},
        {"egressId": "EG", "roomName": "R" * 256, "status": 1},
        {
            "egressId": "EG_first",
            "egress_id": "EG_second",
            "roomName": "room",
            "status": 1,
        },
    ],
)
async def test_list_room_egresses_rejects_unsafe_mapping_identity(item: dict) -> None:
    with pytest.raises(LiveKitRecordingProviderError) as exc_info:
        await build_provider(FakeRoomListEgressClient([item])).list_room_egresses(
            room_name="room-owned"
        )

    assert exc_info.value.error_class == "unknown"


@pytest.mark.anyio
async def test_list_room_egresses_rejects_int_subclass_status() -> None:
    class ProviderStatus(int):
        pass

    item = {
        "egressId": "EG_exact",
        "roomName": "room-owned",
        "status": ProviderStatus(api.EgressStatus.EGRESS_ACTIVE),
    }

    with pytest.raises(LiveKitRecordingProviderError) as exc_info:
        await build_provider(FakeRoomListEgressClient([item])).list_room_egresses(
            room_name="room-owned"
        )

    assert exc_info.value.category == "provider_retryable"
    assert exc_info.value.error_class == "unknown"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "item",
    [
        _SdkRecordAccessFailure(),
        _ProviderDescriptorAccessFailure(),
        _ProviderPresenceProbeFailure(),
    ],
)
async def test_list_room_egresses_contains_record_accessor_errors(
    item: object,
) -> None:
    with pytest.raises(LiveKitRecordingProviderError) as exc_info:
        await build_provider(FakeRoomListEgressClient([item])).list_room_egresses(
            room_name="room-owned"
        )

    assert exc_info.value.category == "provider_retryable"
    assert exc_info.value.error_class == "unknown"
    assert str(exc_info.value) == "provider_retryable"


@pytest.mark.anyio
async def test_list_room_egresses_contains_protocol_classification_failure() -> None:
    with pytest.raises(LiveKitRecordingProviderError) as exc_info:
        await build_provider(
            FakeRoomListEgressClient([_ProviderProtocolClassificationFailure()])
        ).list_room_egresses(room_name="room-owned")

    assert exc_info.value.category == "provider_retryable"
    assert exc_info.value.error_class == "unknown"
    assert str(exc_info.value) == "provider_retryable"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "item",
    [
        _SdkRecordAccessFailure(failure_type=_ProviderAbort),
        _ProviderPresenceProbeFailure(failure_type=_ProviderAbort),
    ],
)
async def test_list_room_egresses_does_not_catch_record_base_exception(
    item: object,
) -> None:
    with pytest.raises(_ProviderAbort):
        await build_provider(FakeRoomListEgressClient([item])).list_room_egresses(
            room_name="room-owned"
        )


@pytest.mark.anyio
async def test_list_room_egresses_does_not_catch_classification_base_exception() -> (
    None
):
    with pytest.raises(_ProviderAbort):
        await build_provider(
            FakeRoomListEgressClient(
                [
                    _ProviderProtocolClassificationFailure(
                        failure_type=_ProviderAbort
                    )
                ]
            )
        ).list_room_egresses(room_name="room-owned")


@pytest.mark.anyio
async def test_list_room_egresses_normalizes_repeated_request_file_output() -> None:
    object_key = "calls/user-1/call-1.ogg"
    client = FakeRoomListEgressClient(
        [
            api.EgressInfo(
                egress_id="egress-repeated-output",
                room_name="room-owned",
                status=api.EgressStatus.EGRESS_STARTING,
                room_composite=api.RoomCompositeEgressRequest(
                    file_outputs=[api.EncodedFileOutput(filepath=object_key)]
                ),
            )
        ]
    )

    snapshots = await build_provider(client).list_room_egresses(room_name="room-owned")

    assert snapshots[0].object_key == object_key


@pytest.mark.anyio
@pytest.mark.parametrize(
    "egress",
    [
        api.EgressInfo(
            egress_id="egress-legacy-file",
            room_name="room-owned",
            status=api.EgressStatus.EGRESS_ACTIVE,
            file=api.FileInfo(filename="calls/user-1/call-1.ogg"),
        ),
        api.EgressInfo(
            egress_id="egress-result-location",
            room_name="room-owned",
            status=api.EgressStatus.EGRESS_COMPLETE,
            file_results=[api.FileInfo(location="calls/user-1/call-1.ogg")],
        ),
    ],
)
async def test_list_room_egresses_normalizes_legacy_file_shapes(
    egress: api.EgressInfo,
) -> None:
    client = FakeRoomListEgressClient([egress])

    snapshots = await build_provider(client).list_room_egresses(room_name="room-owned")

    assert snapshots[0].object_key == "calls/user-1/call-1.ogg"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("location", "expected_object_key"),
    [
        (
            "s3://recordings/calls/user-1/call-1.ogg",
            "calls/user-1/call-1.ogg",
        ),
        (
            "http://minio:9000/recordings/calls/user-1/call-1.ogg",
            "calls/user-1/call-1.ogg",
        ),
        ("s3://different-bucket/calls/user-1/call-1.ogg", None),
        (
            "https://unrecognized.example/recordings/calls/user-1/call-1.ogg",
            None,
        ),
        ("calls/user-1/call-1.ogg", "calls/user-1/call-1.ogg"),
    ],
)
async def test_list_room_egresses_normalizes_location_only_file_result(
    location: str,
    expected_object_key: str | None,
) -> None:
    client = FakeRoomListEgressClient(
        [
            api.EgressInfo(
                egress_id="egress-location-only",
                room_name="room-owned",
                status=api.EgressStatus.EGRESS_COMPLETE,
                file_results=[api.FileInfo(location=location)],
            )
        ]
    )

    snapshots = await build_provider(client).list_room_egresses(room_name="room-owned")

    assert snapshots[0].object_key == expected_object_key


@pytest.mark.anyio
async def test_list_room_egresses_prefers_filename_over_storage_locator() -> None:
    object_key = "calls/user-1/call-1.ogg"
    client = FakeRoomListEgressClient(
        [
            api.EgressInfo(
                egress_id="egress-filename-first",
                room_name="room-owned",
                status=api.EgressStatus.EGRESS_COMPLETE,
                file_results=[
                    api.FileInfo(
                        filename=object_key,
                        location=(
                            "s3://different-bucket/calls/user-1/different-call.ogg"
                        ),
                    )
                ],
            )
        ]
    )

    snapshots = await build_provider(client).list_room_egresses(room_name="room-owned")

    assert snapshots[0].object_key == object_key


@pytest.mark.anyio
async def test_list_room_egresses_rejects_unprovable_location_with_composite_path() -> (
    None
):
    object_key = "calls/user-1/call-1.ogg"
    client = FakeRoomListEgressClient(
        [
            api.EgressInfo(
                egress_id="egress-ambiguous-composite",
                room_name="room-owned",
                status=api.EgressStatus.EGRESS_COMPLETE,
                room_composite=api.RoomCompositeEgressRequest(
                    file=api.EncodedFileOutput(filepath=object_key)
                ),
                file_results=[
                    api.FileInfo(
                        location=(
                            "https://unrecognized.example/recordings/"
                            "calls/user-1/different-call.ogg"
                        )
                    )
                ],
            )
        ]
    )

    snapshots = await build_provider(client).list_room_egresses(room_name="room-owned")

    assert snapshots[0].object_key is None


@pytest.mark.anyio
@pytest.mark.parametrize(
    "unprovable_location",
    [
        "s3://different-bucket/calls/user-1/different-call.ogg",
        ("https://unrecognized.example/recordings/calls/user-1/different-call.ogg"),
    ],
)
async def test_list_room_egresses_rejects_unprovable_location_with_file_result(
    unprovable_location: str,
) -> None:
    object_key = "calls/user-1/call-1.ogg"
    client = FakeRoomListEgressClient(
        [
            api.EgressInfo(
                egress_id="egress-ambiguous-results",
                room_name="room-owned",
                status=api.EgressStatus.EGRESS_COMPLETE,
                file_results=[
                    api.FileInfo(filename=object_key),
                    api.FileInfo(location=unprovable_location),
                ],
            )
        ]
    )

    snapshots = await build_provider(client).list_room_egresses(room_name="room-owned")

    assert snapshots[0].object_key is None


@pytest.mark.anyio
async def test_list_room_egresses_marks_explicit_empty_file_result_invalid() -> None:
    object_key = "calls/user-1/call-1.ogg"
    client = FakeRoomListEgressClient(
        [
            api.EgressInfo(
                egress_id="egress-absent-result-path",
                room_name="room-owned",
                status=api.EgressStatus.EGRESS_COMPLETE,
                room_composite=api.RoomCompositeEgressRequest(
                    file=api.EncodedFileOutput(filepath=object_key)
                ),
                file_results=[api.FileInfo()],
            )
        ]
    )

    snapshots = await build_provider(client).list_room_egresses(room_name="room-owned")

    assert snapshots[0].object_key is None
    evidence = normalized_egress_object_key_evidence(
        client.items[0],
        bucket_name="recordings",
        endpoint_url="http://minio:9000",
    )
    assert evidence.state == "invalid"


@pytest.mark.anyio
async def test_list_room_egresses_sanitizes_malformed_location_only_result() -> None:
    client = FakeRoomListEgressClient(
        [
            api.EgressInfo(
                egress_id="egress-malformed-location",
                room_name="room-owned",
                status=api.EgressStatus.EGRESS_COMPLETE,
                file_results=[api.FileInfo(location="http://[malformed")],
            )
        ]
    )

    snapshots = await build_provider(client).list_room_egresses(room_name="room-owned")

    assert snapshots[0].object_key is None


@pytest.mark.anyio
async def test_list_room_egresses_rejects_malformed_location_with_valid_path() -> None:
    object_key = "calls/user-1/call-1.ogg"
    client = FakeRoomListEgressClient(
        [
            api.EgressInfo(
                egress_id="egress-malformed-with-composite",
                room_name="room-owned",
                status=api.EgressStatus.EGRESS_COMPLETE,
                room_composite=api.RoomCompositeEgressRequest(
                    file=api.EncodedFileOutput(filepath=object_key)
                ),
                file_results=[api.FileInfo(location="http://[malformed")],
            )
        ]
    )

    snapshots = await build_provider(client).list_room_egresses(room_name="room-owned")

    assert snapshots[0].object_key is None


@pytest.mark.anyio
async def test_list_room_egresses_sanitizes_malformed_configured_endpoint() -> None:
    client = FakeRoomListEgressClient(
        [
            api.EgressInfo(
                egress_id="egress-malformed-endpoint",
                room_name="room-owned",
                status=api.EgressStatus.EGRESS_COMPLETE,
                file_results=[
                    api.FileInfo(
                        location=(
                            "http://minio:9000/recordings/calls/user-1/call-1.ogg"
                        )
                    )
                ],
            )
        ]
    )

    snapshots = await build_provider(
        client,
        endpoint_url="http://[malformed",
    ).list_room_egresses(room_name="room-owned")

    assert snapshots[0].object_key is None


@pytest.mark.anyio
async def test_list_room_egresses_fails_closed_for_conflicting_paths() -> None:
    client = FakeRoomListEgressClient(
        [
            api.EgressInfo(
                egress_id="egress-conflicting-output",
                room_name="room-owned",
                status=api.EgressStatus.EGRESS_ACTIVE,
                room_composite=api.RoomCompositeEgressRequest(
                    file=api.EncodedFileOutput(filepath="calls/user-1/call-1.ogg")
                ),
                file_results=[api.FileInfo(filename="calls/user-1/different-call.ogg")],
            )
        ]
    )

    snapshots = await build_provider(client).list_room_egresses(room_name="room-owned")

    assert snapshots[0].object_key is None
