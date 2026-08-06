import asyncio
import io
from functools import lru_cache
from urllib.parse import urlparse

from minio.error import InvalidResponseError, MinioException, S3Error, ServerError
from urllib3 import PoolManager
from urllib3.exceptions import HTTPError, TimeoutError as Urllib3TimeoutError
from urllib3.util import Retry, Timeout

from app.core.config import get_settings
from app.core.observability import Observability, get_observability, instrument_provider
from app.core.provider_failures import (
    ProviderFailure,
    ProviderFailureClass,
    ProviderOperation,
    provider_failure_from_http_status,
)
from app.providers.storage.base import StorageProvider, StoredObject


class StorageConfigurationError(RuntimeError):
    pass


MAX_PRESIGNED_URL_LENGTH = 8192


class S3Storage(StorageProvider):
    def __init__(
        self,
        *,
        bucket_name: str,
        endpoint_url: str,
        access_key: str | None,
        secret_key: str | None,
        region: str | None,
        observability: Observability,
        client=None,
    ) -> None:
        self.bucket_name = bucket_name
        self.endpoint_url = endpoint_url
        self.access_key = access_key
        self.secret_key = secret_key
        self.region = region
        self.client = client
        self._owns_client = client is None
        self._http_client: PoolManager | None = None
        self._bucket_verified = False
        self.observability = observability

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
        self._http_client = http_client
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

    async def aclose(self) -> None:
        http_client = self._http_client
        if http_client is None:
            return
        self._http_client = None
        if self._owns_client:
            self.client = None
        await asyncio.to_thread(http_client.clear)

    def _ensure_bucket_exists(self, client) -> None:
        if self._bucket_verified:
            return
        bucket_exists = client.bucket_exists(self.bucket_name)
        if not bucket_exists:
            raise StorageConfigurationError("Configured storage bucket is unavailable")
        self._bucket_verified = True

    @staticmethod
    def _failure(
        *,
        operation: ProviderOperation,
        disposition: str,
        error_class: ProviderFailureClass,
    ) -> ProviderFailure:
        return ProviderFailure(
            provider="s3",
            operation=operation,
            disposition=disposition,  # type: ignore[arg-type]
            error_class=error_class,
        )

    @classmethod
    def _translate_provider_error(
        cls,
        error: Exception,
        *,
        operation: ProviderOperation,
    ) -> ProviderFailure | None:
        if isinstance(error, S3Error):
            status = getattr(getattr(error, "response", None), "status", None)
            code = error.code
            if code == "RequestTimeout":
                return cls._failure(
                    operation=operation,
                    disposition="retryable",
                    error_class="timeout",
                )
            if code in {"SlowDown", "TooManyRequests"}:
                return cls._failure(
                    operation=operation,
                    disposition="retryable",
                    error_class="rate_limited",
                )
            if code in {"InternalError", "ServiceUnavailable"}:
                return cls._failure(
                    operation=operation,
                    disposition="retryable",
                    error_class="unavailable",
                )
            if code in {
                "AccessDenied",
                "InvalidAccessKeyId",
                "InvalidToken",
                "SignatureDoesNotMatch",
            }:
                return cls._failure(
                    operation=operation,
                    disposition="terminal",
                    error_class="authentication",
                )
            if code in {"BucketAlreadyExists", "BucketAlreadyOwnedByYou"}:
                return cls._failure(
                    operation=operation,
                    disposition="terminal",
                    error_class="conflict",
                )
            if code in {"NoSuchKey", "NoSuchObject", "NoSuchVersion"}:
                return cls._failure(
                    operation=operation,
                    disposition="terminal",
                    error_class="not_found",
                )
            if code in {"InvalidArgument", "InvalidRequest"}:
                return cls._failure(
                    operation=operation,
                    disposition="terminal",
                    error_class="validation",
                )
            if isinstance(status, int):
                return provider_failure_from_http_status(
                    provider="s3",
                    operation=operation,
                    status=status,
                )
            return cls._failure(
                operation=operation,
                disposition="terminal",
                error_class="unknown",
            )
        if isinstance(error, InvalidResponseError):
            return cls._failure(
                operation=operation,
                disposition="terminal",
                error_class="validation",
            )
        if isinstance(error, ServerError):
            return provider_failure_from_http_status(
                provider="s3",
                operation=operation,
                status=error.status_code,
            )
        if isinstance(error, MinioException):
            return cls._failure(
                operation=operation,
                disposition="terminal",
                error_class="unknown",
            )
        if isinstance(error, (TimeoutError, Urllib3TimeoutError)):
            return cls._failure(
                operation=operation,
                disposition="retryable",
                error_class="timeout",
            )
        if isinstance(error, (ConnectionError, OSError, HTTPError)):
            return cls._failure(
                operation=operation,
                disposition="retryable",
                error_class="unavailable",
            )
        return None

    @staticmethod
    def _raise_configuration_error(error: S3Error) -> None:
        if error.code == "NoSuchBucket":
            raise StorageConfigurationError(
                "Configured storage bucket is unavailable"
            ) from error

    @classmethod
    def _validate_presigned_url(cls, value: object) -> str:
        if (
            type(value) is not str
            or not value
            or len(value) > MAX_PRESIGNED_URL_LENGTH
            or any(
                character.isspace()
                or ord(character) < 32
                or ord(character) == 127
                for character in value
            )
        ):
            raise cls._failure(
                operation="get_download_url",
                disposition="terminal",
                error_class="validation",
            ) from None
        try:
            parsed = urlparse(value)
            valid_location = parsed.hostname is not None
            valid_scheme = parsed.scheme in {"http", "https"}
        except ValueError:
            valid_location = False
            valid_scheme = False
        if not valid_scheme or not valid_location:
            raise cls._failure(
                operation="get_download_url",
                disposition="terminal",
                error_class="validation",
            ) from None
        return value

    async def _run_application_call(
        self,
        provider_operation: ProviderOperation,
        operation,
        *args,
        **kwargs,
    ):
        try:
            return await asyncio.to_thread(operation, *args, **kwargs)
        except StorageConfigurationError:
            raise
        except Exception as exc:
            if isinstance(exc, S3Error):
                self._raise_configuration_error(exc)
            failure = self._translate_provider_error(
                exc,
                operation=provider_operation,
            )
            if failure is None:
                raise
            raise failure from exc

    @instrument_provider("s3", "upload_bytes")
    async def upload_bytes(
        self, *, object_key: str, data: bytes, content_type: str
    ) -> StoredObject:
        client = self._get_client()
        await self._run_application_call(
            "upload_bytes", self._ensure_bucket_exists, client
        )
        data_stream = io.BytesIO(data)
        await self._run_application_call(
            "upload_bytes",
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

    @instrument_provider("s3", "get_download_url")
    async def get_download_url(self, *, object_key: str) -> str | None:
        client = self._get_client()
        await self._run_application_call(
            "get_download_url", self._ensure_bucket_exists, client
        )
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
                ) from exc
            if exc.code in {"NoSuchKey", "NoSuchObject", "NoSuchVersion"}:
                return None
            failure = self._translate_provider_error(exc, operation="get_download_url")
            if failure is not None:
                raise failure from exc
            raise
        except Exception as exc:
            failure = self._translate_provider_error(exc, operation="get_download_url")
            if failure is not None:
                raise failure from exc
            raise
        download_url = await self._run_application_call(
            "get_download_url",
            client.presigned_get_object,
            self.bucket_name,
            object_key,
        )
        return self._validate_presigned_url(download_url)

    @instrument_provider("s3", "delete_object")
    async def delete_object(self, *, object_key: str) -> None:
        client = self._get_client()
        await self._run_application_call(
            "delete_object", self._ensure_bucket_exists, client
        )
        try:
            await asyncio.to_thread(
                client.remove_object,
                self.bucket_name,
                object_key,
            )
        except S3Error as exc:
            if exc.code == "NoSuchBucket":
                raise StorageConfigurationError(
                    "Configured storage bucket is unavailable"
                ) from exc
            if exc.code in {"NoSuchKey", "NoSuchObject", "NoSuchVersion"}:
                return
            failure = self._translate_provider_error(exc, operation="delete_object")
            if failure is not None:
                raise failure from exc
            raise
        except Exception as exc:
            failure = self._translate_provider_error(exc, operation="delete_object")
            if failure is not None:
                raise failure from exc
            raise

    @instrument_provider("s3", "get_bucket_lifecycle")
    async def get_bucket_lifecycle(self):
        client = self._get_client()
        await self._run_application_call(
            "get_bucket_lifecycle", self._ensure_bucket_exists, client
        )
        return await self._run_application_call(
            "get_bucket_lifecycle",
            client.get_bucket_lifecycle,
            self.bucket_name,
        )


@lru_cache()
def get_s3_storage() -> S3Storage:
    settings = get_settings()
    return S3Storage(
        bucket_name=settings.storage_bucket_name,
        endpoint_url=settings.s3_endpoint_url or "http://minio:9000",
        access_key=settings.s3_access_key,
        secret_key=settings.s3_secret_key,
        region=settings.s3_region,
        observability=get_observability(),
    )
