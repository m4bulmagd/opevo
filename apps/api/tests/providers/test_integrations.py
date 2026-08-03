import asyncio
import time
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from minio.error import InvalidResponseError, MinioException, S3Error, ServerError
from urllib3.exceptions import MaxRetryError, ReadTimeoutError

from app.core.provider_failures import ProviderFailure
from app.providers.notifications.firebase import FirebaseNotificationProvider
from app.providers.storage import s3 as s3_module
from app.providers.storage.s3 import S3Storage, StorageConfigurationError
from app.providers.telephony.twilio import TelephonyTwilio


class _StorageTelemetry:
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


class FakeMinioClient:
    def __init__(self, *, bucket_present: bool = True) -> None:
        self.bucket_present = bucket_present
        self.created_buckets: list[str] = []
        self.bucket_exists_calls: list[str] = []
        self.put_calls: list[dict] = []
        self.stat_calls: list[dict] = []
        self.presigned_calls: list[dict] = []
        self.missing_objects: set[str] = set()
        self.missing_bucket_on: set[str] = set()

    def _raise_missing_bucket(
        self, operation: str, object_key: str | None = None
    ) -> None:
        if operation not in self.missing_bucket_on:
            return
        from minio.error import S3Error

        raise S3Error(
            None,
            "NoSuchBucket",
            "provider-controlled missing bucket message",
            self.bucket_name if hasattr(self, "bucket_name") else "recordings",
            "request-id",
            "host-id",
            "recordings",
            object_key,
        )

    def bucket_exists(self, bucket_name: str) -> bool:
        self.bucket_exists_calls.append(bucket_name)
        return self.bucket_present

    def make_bucket(self, bucket_name: str) -> None:
        raise AssertionError("application storage paths must not create buckets")

    def put_object(
        self, bucket_name: str, object_key: str, data, length: int, content_type: str
    ) -> None:
        self._raise_missing_bucket("put", object_key)
        self.put_calls.append(
            {
                "bucket_name": bucket_name,
                "object_key": object_key,
                "data": data.read(),
                "length": length,
                "content_type": content_type,
            }
        )

    def presigned_get_object(self, bucket_name: str, object_key: str) -> str:
        self._raise_missing_bucket("sign", object_key)
        self.presigned_calls.append(
            {
                "bucket_name": bucket_name,
                "object_key": object_key,
            }
        )
        return f"http://minio:9000/{bucket_name}/{object_key}?signed=fresh"

    def stat_object(self, bucket_name: str, object_key: str):
        self._raise_missing_bucket("stat", object_key)
        self.stat_calls.append({"bucket_name": bucket_name, "object_key": object_key})
        if object_key in self.missing_objects:
            from minio.error import S3Error

            raise S3Error(
                None,
                "NoSuchKey",
                "provider-controlled missing object message",
                object_key,
                "request-id",
                "host-id",
                bucket_name,
                object_key,
            )
        return object()


class BlockingMinioClient(FakeMinioClient):
    def bucket_exists(self, bucket_name: str) -> bool:
        time.sleep(0.1)
        return super().bucket_exists(bucket_name)


def _s3_error(*, code: str, status: int) -> S3Error:
    return S3Error(
        SimpleNamespace(status=status),
        code,
        "provider-controlled credential and phone +33123456789",
        "provider-controlled-resource",
        "provider-controlled-request-id",
        "provider-controlled-host-id",
    )


class FailingOperationMinioClient(FakeMinioClient):
    def __init__(self, *, operation: str, error: Exception) -> None:
        super().__init__()
        self.operation = operation
        self.error = error

    def _fail(self, operation: str) -> None:
        if self.operation == operation:
            raise self.error

    def bucket_exists(self, bucket_name: str) -> bool:
        self._fail("bucket")
        return super().bucket_exists(bucket_name)

    def put_object(self, *args, **kwargs) -> None:
        self._fail("put")
        return super().put_object(*args, **kwargs)

    def stat_object(self, *args, **kwargs):
        self._fail("stat")
        return super().stat_object(*args, **kwargs)

    def presigned_get_object(self, *args, **kwargs) -> str:
        self._fail("sign")
        return super().presigned_get_object(*args, **kwargs)

    def get_bucket_lifecycle(self, bucket_name: str):
        self._fail("lifecycle")
        return object()


class SuccessfulPresignedResultMinioClient(FakeMinioClient):
    def __init__(self, result: object) -> None:
        super().__init__()
        self.result = result

    def presigned_get_object(self, bucket_name: str, object_key: str) -> object:
        super().presigned_get_object(bucket_name, object_key)
        return self.result


class _PresignedURLStringSubclass(str):
    pass


@pytest.mark.anyio
async def test_s3_storage_uploads_recordings_with_env_backed_endpoint() -> None:
    client = FakeMinioClient()
    telemetry = _StorageTelemetry()
    storage = S3Storage(
        bucket_name="recordings",
        endpoint_url="http://minio:9000",
        access_key="minioadmin",
        secret_key="minioadmin",
        region="us-east-1",
        client=client,
        observability=telemetry,
    )

    stored = await storage.upload_bytes(
        object_key="calls/user-123/call-456.mp3",
        data=b"audio-bytes",
        content_type="audio/mpeg",
    )

    assert client.created_buckets == []
    assert client.bucket_exists_calls == ["recordings"]
    assert client.put_calls == [
        {
            "bucket_name": "recordings",
            "object_key": "calls/user-123/call-456.mp3",
            "data": b"audio-bytes",
            "length": 11,
            "content_type": "audio/mpeg",
        }
    ]
    assert stored.url == "http://minio:9000/recordings/calls/user-123/call-456.mp3"
    assert telemetry.calls == [("s3", "upload_bytes", "success")]


@pytest.mark.anyio
async def test_s3_storage_mints_fresh_download_url() -> None:
    client = FakeMinioClient()
    storage = S3Storage(
        bucket_name="recordings",
        endpoint_url="http://minio:9000",
        access_key="minioadmin",
        secret_key="minioadmin",
        region="us-east-1",
        client=client,
    )

    signed_url = await storage.get_download_url(
        object_key="calls/user-123/call-456.mp3"
    )

    assert client.created_buckets == []
    assert client.stat_calls == [
        {
            "bucket_name": "recordings",
            "object_key": "calls/user-123/call-456.mp3",
        }
    ]
    assert client.presigned_calls == [
        {
            "bucket_name": "recordings",
            "object_key": "calls/user-123/call-456.mp3",
        }
    ]
    assert (
        signed_url
        == "http://minio:9000/recordings/calls/user-123/call-456.mp3?signed=fresh"
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "result",
    [
        None,
        42,
        _PresignedURLStringSubclass("https://storage.example.test/private.mp3"),
        "",
        "   ",
        "relative/download/path",
        "ftp://storage.example.test/recording.mp3",
        "https://",
        "https://storage.example.test/PRIVATE_URL_SENTINEL\x00.mp3",
        "https://storage.example.test/" + ("a" * 8192),
    ],
)
async def test_s3_storage_rejects_malformed_successful_presigned_urls(
    result: object,
) -> None:
    storage = S3Storage(
        bucket_name="recordings",
        client=SuccessfulPresignedResultMinioClient(result),
    )

    with pytest.raises(ProviderFailure) as exc_info:
        await storage.get_download_url(object_key="calls/private.mp3")

    assert (
        exc_info.value.provider,
        exc_info.value.operation,
        exc_info.value.disposition,
        exc_info.value.error_class,
    ) == ("s3", "get_download_url", "terminal", "validation")
    assert "PRIVATE_URL_SENTINEL" not in str(exc_info.value)
    assert "PRIVATE_URL_SENTINEL" not in repr(exc_info.value)
    assert all(
        "PRIVATE_URL_SENTINEL" not in str(value) for value in exc_info.value.args
    )


@pytest.mark.anyio
@pytest.mark.parametrize("defect_type", [TypeError, RuntimeError, AttributeError])
async def test_s3_presigning_defects_propagate_exact_identity(
    monkeypatch: pytest.MonkeyPatch,
    defect_type: type[Exception],
) -> None:
    async def run_inline(operation, *args, **kwargs):
        return operation(*args, **kwargs)

    defect = defect_type("S3_PRESIGN_DEFECT_SENTINEL")
    monkeypatch.setattr(s3_module.asyncio, "to_thread", run_inline)
    storage = S3Storage(
        bucket_name="recordings",
        client=FailingOperationMinioClient(operation="sign", error=defect),
    )

    with pytest.raises(defect_type) as exc_info:
        await storage.get_download_url(object_key="calls/private.mp3")

    assert exc_info.value is defect


@pytest.mark.anyio
async def test_s3_storage_returns_none_for_missing_object_without_signing() -> None:
    client = FakeMinioClient()
    client.missing_objects.add("calls/missing.mp3")
    storage = S3Storage(bucket_name="recordings", client=client)

    signed_url = await storage.get_download_url(object_key="calls/missing.mp3")

    assert signed_url is None
    assert client.presigned_calls == []


@pytest.mark.anyio
async def test_s3_storage_treats_missing_bucket_as_configuration_failure() -> None:
    client = FakeMinioClient(bucket_present=False)
    storage = S3Storage(bucket_name="recordings", client=client)

    with pytest.raises(RuntimeError, match="Configured storage bucket is unavailable"):
        await storage.get_download_url(object_key="calls/missing.mp3")

    assert client.created_buckets == []
    assert client.presigned_calls == []


@pytest.mark.anyio
@pytest.mark.parametrize("operation", ["put", "stat", "sign"])
async def test_s3_storage_maps_missing_bucket_races_to_configuration_failure(
    operation: str,
) -> None:
    client = FakeMinioClient()
    client.missing_bucket_on.add(operation)
    storage = S3Storage(bucket_name="recordings", client=client)

    with pytest.raises(
        StorageConfigurationError,
        match="Configured storage bucket is unavailable",
    ):
        if operation == "put":
            await storage.upload_bytes(
                object_key="calls/race.mp3",
                data=b"audio",
                content_type="audio/mpeg",
            )
        else:
            await storage.get_download_url(object_key="calls/race.mp3")


@pytest.mark.anyio
@pytest.mark.parametrize(
    (
        "operation",
        "provider_error",
        "expected_provider_operation",
        "expected_disposition",
        "expected_error_class",
    ),
    [
        (
            "bucket",
            TimeoutError("provider-controlled timeout"),
            "upload_bytes",
            "retryable",
            "timeout",
        ),
        (
            "put",
            _s3_error(code="SlowDown", status=429),
            "upload_bytes",
            "retryable",
            "rate_limited",
        ),
        (
            "lifecycle",
            _s3_error(code="InternalError", status=503),
            "get_bucket_lifecycle",
            "retryable",
            "unavailable",
        ),
        (
            "stat",
            _s3_error(code="AccessDenied", status=403),
            "get_download_url",
            "terminal",
            "authentication",
        ),
        (
            "sign",
            _s3_error(code="InvalidArgument", status=400),
            "get_download_url",
            "terminal",
            "validation",
        ),
        (
            "put",
            _s3_error(code="NoSuchKey", status=404),
            "upload_bytes",
            "terminal",
            "not_found",
        ),
        (
            "put",
            _s3_error(code="BucketAlreadyExists", status=409),
            "upload_bytes",
            "terminal",
            "conflict",
        ),
        (
            "put",
            MinioException("S3_MINIO_EXCEPTION_SENTINEL"),
            "upload_bytes",
            "terminal",
            "unknown",
        ),
        (
            "put",
            ReadTimeoutError(None, "https://minio.invalid", "S3_TIMEOUT_SENTINEL"),
            "upload_bytes",
            "retryable",
            "timeout",
        ),
        (
            "put",
            MaxRetryError(None, "https://minio.invalid"),
            "upload_bytes",
            "retryable",
            "unavailable",
        ),
    ],
)
async def test_s3_storage_translates_known_sdk_and_transport_failures_once(
    operation: str,
    provider_error: Exception,
    expected_provider_operation: str,
    expected_disposition: str,
    expected_error_class: str,
) -> None:
    client = FailingOperationMinioClient(
        operation=operation,
        error=provider_error,
    )
    storage = S3Storage(bucket_name="recordings", client=client)

    with pytest.raises(ProviderFailure) as exc_info:
        if operation == "put" or operation == "bucket":
            await storage.upload_bytes(
                object_key="calls/provider-failure.mp3",
                data=b"audio",
                content_type="audio/mpeg",
            )
        elif operation in {"stat", "sign"}:
            await storage.get_download_url(object_key="calls/provider-failure.mp3")
        else:
            await storage.get_bucket_lifecycle()

    assert exc_info.value.provider == "s3"
    assert exc_info.value.operation == expected_provider_operation
    assert exc_info.value.disposition == expected_disposition
    assert exc_info.value.retryable is (expected_disposition == "retryable")
    assert exc_info.value.error_class == expected_error_class
    assert str(exc_info.value) == "provider operation failed"
    assert "provider-controlled" not in str(exc_info.value)
    assert exc_info.value.__cause__ is provider_error


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("provider_error", "expected_disposition", "expected_error_class"),
    [
        (
            InvalidResponseError(
                503,
                "text/plain",
                "S3_RESPONSE_BODY_SENTINEL +33123456789",
            ),
            "terminal",
            "validation",
        ),
        (
            ServerError("S3_RESPONSE_BODY_SENTINEL +33123456789", 503),
            "retryable",
            "unavailable",
        ),
    ],
)
async def test_s3_storage_keeps_malformed_provider_responses_private(
    provider_error: Exception,
    expected_disposition: str,
    expected_error_class: str,
) -> None:
    storage = S3Storage(
        bucket_name="recordings",
        client=FailingOperationMinioClient(
            operation="put",
            error=provider_error,
        ),
    )

    with pytest.raises(ProviderFailure) as exc_info:
        await storage.upload_bytes(
            object_key="calls/provider-http-failure.mp3",
            data=b"audio",
            content_type="audio/mpeg",
        )

    assert (
        exc_info.value.provider,
        exc_info.value.operation,
        exc_info.value.disposition,
        exc_info.value.error_class,
    ) == ("s3", "upload_bytes", expected_disposition, expected_error_class)
    assert "S3_RESPONSE_BODY_SENTINEL" not in str(exc_info.value)
    assert exc_info.value.__cause__ is provider_error


@pytest.mark.anyio
@pytest.mark.parametrize(
    "provider_error",
    [TypeError("S3_DEFECT_SENTINEL"), RuntimeError("S3_DEFECT_SENTINEL")],
)
async def test_s3_storage_does_not_translate_injected_programming_defects(
    monkeypatch: pytest.MonkeyPatch,
    provider_error: Exception,
) -> None:
    async def run_inline(operation, *args, **kwargs):
        return operation(*args, **kwargs)

    monkeypatch.setattr(s3_module.asyncio, "to_thread", run_inline)
    storage = S3Storage(
        bucket_name="recordings",
        client=FailingOperationMinioClient(operation="put", error=provider_error),
    )

    with pytest.raises(type(provider_error), match="S3_DEFECT_SENTINEL"):
        await storage.upload_bytes(
            object_key="calls/provider-defect.mp3",
            data=b"audio",
            content_type="audio/mpeg",
        )


@pytest.mark.anyio
async def test_s3_storage_propagates_cancellation_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def cancel_inline(*_args, **_kwargs):
        raise asyncio.CancelledError

    monkeypatch.setattr(s3_module.asyncio, "to_thread", cancel_inline)
    storage = S3Storage(bucket_name="recordings", client=FakeMinioClient())

    with pytest.raises(asyncio.CancelledError):
        await storage.upload_bytes(
            object_key="calls/cancelled.mp3",
            data=b"audio",
            content_type="audio/mpeg",
        )


@pytest.mark.anyio
async def test_s3_storage_calls_do_not_block_the_event_loop() -> None:
    client = BlockingMinioClient()
    storage = S3Storage(bucket_name="recordings", client=client)
    heartbeat = asyncio.create_task(asyncio.sleep(0.02))

    await storage.upload_bytes(
        object_key="calls/user-123/call-456.mp3",
        data=b"audio-bytes",
        content_type="audio/mpeg",
    )

    assert heartbeat.done()
    await heartbeat


def test_s3_storage_uses_bounded_network_policy() -> None:
    storage = S3Storage(
        bucket_name="recordings",
        endpoint_url="http://minio:9000",
        access_key="minioadmin",
        secret_key="minioadmin",
    )

    client = storage._build_client()

    assert client._http.connection_pool_kw["timeout"].connect_timeout == 5
    assert client._http.connection_pool_kw["timeout"].read_timeout == 30
    assert client._http.connection_pool_kw["retries"].total == 2


@pytest.mark.anyio
async def test_firebase_provider_remains_disabled_without_private_tokens() -> None:
    provider = FirebaseNotificationProvider()

    status = await provider.send_notification(
        user_id="user_123",
        notification_type="call_completed",
        payload={
            "summary_text": "Caller asked about opening hours.",
            "minutes_charged": 2,
        },
    )

    assert status == "disabled"


@pytest.mark.anyio
async def test_dormant_twilio_release_is_explicitly_unsupported() -> None:
    with pytest.raises(NotImplementedError):
        await TelephonyTwilio().release_number(provider_number_id="pn_123")
