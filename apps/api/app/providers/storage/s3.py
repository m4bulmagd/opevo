import asyncio
import io
from functools import lru_cache
from urllib.parse import urlparse

from app.core.config import get_settings
from app.providers.storage.base import StorageProvider
from app.providers.storage.base import StoredObject


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
        return Minio(
            endpoint,
            access_key=self.access_key,
            secret_key=self.secret_key,
            secure=secure,
            region=self.region,
        )

    def _get_client(self):
        if self.client is None:
            self.client = self._build_client()
        return self.client

    def _ensure_bucket_exists(self, client) -> None:
        if self._bucket_verified:
            return
        if not client.bucket_exists(self.bucket_name):
            client.make_bucket(self.bucket_name)
        self._bucket_verified = True

    async def upload_bytes(self, *, object_key: str, data: bytes, content_type: str) -> StoredObject:
        client = self._get_client()
        await asyncio.to_thread(self._ensure_bucket_exists, client)
        data_stream = io.BytesIO(data)
        await asyncio.to_thread(
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

    async def get_download_url(self, *, object_key: str) -> str:
        client = self._get_client()
        await asyncio.to_thread(self._ensure_bucket_exists, client)
        return await asyncio.to_thread(
            client.presigned_get_object,
            self.bucket_name,
            object_key,
        )


@lru_cache()
def get_s3_storage() -> S3Storage:
    return S3Storage()
