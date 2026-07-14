import asyncio
import time
from types import SimpleNamespace

import pytest
from minio.error import InvalidResponseError, S3Error, ServerError

from app.providers.notifications.firebase import FirebaseNotificationProvider
from app.providers.storage.base import StorageProviderError
from app.providers.storage.s3 import S3Storage, StorageConfigurationError


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

    def _raise_missing_bucket(self, operation: str, object_key: str | None = None) -> None:
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

    def put_object(self, bucket_name: str, object_key: str, data, length: int, content_type: str) -> None:
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
        self.stat_calls.append(
            {"bucket_name": bucket_name, "object_key": object_key}
        )
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


@pytest.mark.anyio
async def test_s3_storage_uploads_recordings_with_env_backed_endpoint() -> None:
    client = FakeMinioClient()
    storage = S3Storage(
        bucket_name="recordings",
        endpoint_url="http://minio:9000",
        access_key="minioadmin",
        secret_key="minioadmin",
        region="us-east-1",
        client=client,
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

    signed_url = await storage.get_download_url(object_key="calls/user-123/call-456.mp3")

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
    assert signed_url == "http://minio:9000/recordings/calls/user-123/call-456.mp3?signed=fresh"


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
    ("operation", "provider_error", "expected_category"),
    [
        ("bucket", TimeoutError("provider-controlled timeout"), "provider_retryable"),
        ("put", _s3_error(code="SlowDown", status=429), "provider_retryable"),
        ("lifecycle", _s3_error(code="InternalError", status=503), "provider_retryable"),
        ("stat", _s3_error(code="AccessDenied", status=403), "provider_terminal"),
        ("sign", _s3_error(code="InvalidArgument", status=400), "provider_terminal"),
        ("put", _s3_error(code="NoSuchKey", status=404), "provider_terminal"),
    ],
)
async def test_s3_storage_uses_safe_fixed_provider_categories(
    operation: str,
    provider_error: Exception,
    expected_category: str,
) -> None:
    client = FailingOperationMinioClient(
        operation=operation,
        error=provider_error,
    )
    storage = S3Storage(bucket_name="recordings", client=client)

    with pytest.raises(StorageProviderError) as exc_info:
        if operation == "put" or operation == "bucket":
            await storage.upload_bytes(
                object_key="calls/provider-failure.mp3",
                data=b"audio",
                content_type="audio/mpeg",
            )
        elif operation in {"stat", "sign"}:
            await storage.get_download_url(
                object_key="calls/provider-failure.mp3"
            )
        else:
            await storage.get_bucket_lifecycle()

    assert exc_info.value.category == expected_category
    assert exc_info.value.retryable is (expected_category == "provider_retryable")
    assert str(exc_info.value) == expected_category
    assert "provider-controlled" not in str(exc_info.value)
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__suppress_context__ is True


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("provider_error", "expected_category"),
    [
        (
            InvalidResponseError(
                status_code,
                "text/plain",
                "provider-controlled credential and phone +33123456789",
            ),
            "provider_terminal" if status_code in {400, 401, 403} else "provider_retryable",
        )
        for status_code in (400, 401, 403, 429, 500, 503)
    ]
    + [
        (
            ServerError(
                "provider-controlled credential and phone +33123456789",
                status_code,
            ),
            "provider_terminal" if status_code in {400, 401, 403} else "provider_retryable",
        )
        for status_code in (400, 401, 403, 429, 500, 503)
    ],
)
async def test_s3_storage_maps_pinned_minio_http_exceptions_safely(
    provider_error: Exception,
    expected_category: str,
) -> None:
    storage = S3Storage(
        bucket_name="recordings",
        client=FailingOperationMinioClient(
            operation="put",
            error=provider_error,
        ),
    )

    with pytest.raises(StorageProviderError) as exc_info:
        await storage.upload_bytes(
            object_key="calls/provider-http-failure.mp3",
            data=b"audio",
            content_type="audio/mpeg",
        )

    assert exc_info.value.category == expected_category
    assert exc_info.value.retryable is (expected_category == "provider_retryable")
    assert str(exc_info.value) == expected_category
    assert "provider-controlled" not in str(exc_info.value)
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__suppress_context__ is True


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
        payload={"summary_text": "Caller asked about opening hours.", "minutes_charged": 2},
    )

    assert status == "disabled"
