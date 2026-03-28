from dataclasses import dataclass
from uuid import UUID

from app.providers.storage.base import StorageProvider
from app.providers.storage.s3 import S3Storage


@dataclass(frozen=True)
class RecordingResult:
    object_key: str | None
    url: str | None
    job_enqueued: bool


class RecordingService:
    def __init__(self, provider: StorageProvider | None = None) -> None:
        self.provider = provider or S3Storage()

    async def store_recording(self, payload: dict) -> RecordingResult:
        recording_bytes = payload.get("recording_bytes")
        if not recording_bytes:
            return RecordingResult(object_key=None, url=None, job_enqueued=False)

        object_key = f"calls/{payload['user_id']}/{payload['call_id']}.mp3"
        stored_object = await self.provider.upload_bytes(
            object_key=object_key,
            data=recording_bytes,
            content_type="audio/mpeg",
        )
        return RecordingResult(
            object_key=stored_object.object_key,
            url=stored_object.url,
            job_enqueued=True,
        )

    async def get_access_url(
        self,
        *,
        call_id: UUID,
        user_id: UUID,
        stored_url: str | None,
    ) -> str | None:
        if not stored_url:
            return None
        object_key = f"calls/{user_id}/{call_id}.mp3"
        return await self.provider.get_download_url(object_key=object_key)
