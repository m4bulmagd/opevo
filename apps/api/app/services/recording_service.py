from uuid import UUID

from fastapi import Depends

from app.providers.storage.base import StorageProvider
from app.providers.storage.s3 import S3Storage, get_s3_storage

class RecordingService:
    def __init__(self, provider: StorageProvider) -> None:
        self.provider = provider

    async def get_access_url(
        self,
        *,
        call_id: UUID,
        user_id: UUID,
        recording_object_key: str | None,
    ) -> str | None:
        if not recording_object_key:
            return None
        try:
            return await self.provider.get_download_url(object_key=recording_object_key)
        except FileNotFoundError:
            return None


def get_recording_service(
    storage_provider: S3Storage = Depends(get_s3_storage),
) -> RecordingService:
    return RecordingService(provider=storage_provider)
