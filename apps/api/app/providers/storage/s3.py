import asyncio
import io
from functools import lru_cache
from urllib.parse import urlparse

from minio.error import InvalidResponseError, S3Error, ServerError
from urllib3 import PoolManager
from urllib3.util import Retry, Timeout

from app.core.config import get_settings
from app.providers.storage.base import StorageProvider, StorageProviderError, StoredObject


class StorageConfigurationError(RuntimeError):
    pass


class S3Storage(StorageProvider):
    def __init__(
        self,
        *,
        bucket_name: str | None = None,
        endpoint_url: str | None = None,
        access_key: str | None = None,
        secret_key: str | None = None,
        region: str | None = None,
        client=None,
    ) -> None:
        settings = get_settings()
        self.bucket_name = bucket_name or settings.storage_bucket_name
        self.endpoint_url = endpoint_url or settings.s3_endpoint_url or "http://minio:9000"
        self.access_key = access_key or settings.s3_access_key
        self.secret_key = secret_key or settings.s3_secret_key
        self.region = region or settings.s3_region
        self.client = client
        self._bucket_verified = False

    def _build_client(self):
        from minio import Minio

        parsed = urlparse(self.endpoint_url)
        endpoint = parsed.netloc or parsed.path
        secure = parsed.scheme == "https"
        http_client = PoolManager(
            timeout=Timeout(connect=5, read=30),
            retries=Retry(
                total=2,
                connect=2,
                read=2,
                status=2,
                status_forcelist=(429, 500, 502, 503, 504),
                backoff_factor=0.2,
            ),
        )
        return Minio(
            endpoint,
            access_key=self.access_key,
            secret_key=self.secret_key,
            secure=secure,
            region=self.region,
            http_client=http_client,
        )

    def _get_client(self):
        if self.client is None:
            self.client = self._build_client()
        return self.client

    def _ensure_bucket_exists(self, client) -> None:
        if self._bucket_verified:
            return
        bucket_exists = client.bucket_exists(self.bucket_name)
        if not bucket_exists:
            raise StorageConfigurationError(
                "Configured storage bucket is unavailable"
            )
        self._bucket_verified = True

    @staticmethod
    def _raise_provider_error(error: Exception) -> None:
        if isinstance(error, S3Error) and error.code == "NoSuchBucket":
            raise StorageConfigurationError(
                "Configured storage bucket is unavailable"
            ) from None
        if isinstance(error, S3Error):
            status = getattr(getattr(error, "response", None), "status", None)
            retryable_codes = {
                "InternalError",
                "RequestTimeout",
                "ServiceUnavailable",
                "SlowDown",
                "TooManyRequests",
            }
            category = (
                "provider_retryable"
                if status == 429
                or (isinstance(status, int) and status >= 500)
                or error.code in retryable_codes
                else "provider_terminal"
            )
        elif isinstance(error, InvalidResponseError):
            status = error._code
            category = (
                "provider_retryable"
                if status == 429 or status >= 500
                else "provider_terminal"
            )
        elif isinstance(error, ServerError):
            status = error.status_code
            category = (
                "provider_retryable"
                if status == 429 or status >= 500
                else "provider_terminal"
            )
        elif isinstance(error, (TimeoutError, ConnectionError, OSError)):
            category = "provider_retryable"
        elif isinstance(error, (TypeError, ValueError)):
            category = "provider_terminal"
        else:
            category = "provider_retryable"
        raise StorageProviderError(category) from None

    async def _run_application_call(self, operation, *args, **kwargs):
        try:
            return await asyncio.to_thread(operation, *args, **kwargs)
        except StorageConfigurationError:
            raise
        except Exception as exc:
            self._raise_provider_error(exc)

    async def upload_bytes(self, *, object_key: str, data: bytes, content_type: str) -> StoredObject:
        client = self._get_client()
        await self._run_application_call(self._ensure_bucket_exists, client)
        data_stream = io.BytesIO(data)
        await self._run_application_call(
            client.put_object,
            self.bucket_name,
            object_key,
            data_stream,
            len(data),
            content_type=content_type,
        )
        return StoredObject(
            object_key=object_key,
            url=f"{self.endpoint_url.rstrip('/')}/{self.bucket_name}/{object_key}",
        )

    async def get_download_url(self, *, object_key: str) -> str | None:
        client = self._get_client()
        await self._run_application_call(self._ensure_bucket_exists, client)
        try:
            await asyncio.to_thread(
                client.stat_object,
                self.bucket_name,
                object_key,
            )
        except S3Error as exc:
            if exc.code == "NoSuchBucket":
                raise StorageConfigurationError(
                    "Configured storage bucket is unavailable"
                ) from None
            if exc.code in {"NoSuchKey", "NoSuchObject", "NoSuchVersion"}:
                return None
            self._raise_provider_error(exc)
        except Exception as exc:
            self._raise_provider_error(exc)
        return await self._run_application_call(
            client.presigned_get_object,
            self.bucket_name,
            object_key,
        )

    async def get_bucket_lifecycle(self):
        client = self._get_client()
        await self._run_application_call(self._ensure_bucket_exists, client)
        return await self._run_application_call(
            client.get_bucket_lifecycle,
            self.bucket_name,
        )


@lru_cache()
def get_s3_storage() -> S3Storage:
    return S3Storage()
