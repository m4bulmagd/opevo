from app.core.config import get_settings
from app.providers.storage.base import StorageProvider
from app.providers.storage.base import StoredObject


class S3Storage(StorageProvider):
    def __init__(self, *, bucket_name: str | None = None) -> None:
        settings = get_settings()
        self.bucket_name = bucket_name or settings.storage_bucket_name

    async def upload_bytes(self, *, object_key: str, data: bytes, content_type: str) -> StoredObject:
        return StoredObject(
            object_key=object_key,
            url=f"s3://{self.bucket_name}/{object_key}",
        )
