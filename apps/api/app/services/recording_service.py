import logging
from dataclasses import dataclass
from uuid import UUID

from fastapi import Depends

from app.core.logging import report_safe_exception
from app.providers.storage.base import StorageProvider
from app.providers.storage.s3 import S3Storage, get_s3_storage


@dataclass(frozen=True)
class RecordingResult:
    object_key: str | None
    url: str | None
    job_enqueued: bool


logger = logging.getLogger(__name__)


class RecordingService:
    def __init__(self, provider: StorageProvider) -> None:
        self.provider = provider

    async def store_recording(self, payload: dict) -> RecordingResult:
        recording_bytes = payload.get("recording_bytes")
        if not recording_bytes:
            return RecordingResult(object_key=None, url=None, job_enqueued=False)

        object_key = f"calls/{payload['user_id']}/{payload['call_id']}.mp3"
        try:
            stored_object = await self.provider.upload_bytes(
                object_key=object_key,
                data=recording_bytes,
                content_type="audio/mpeg",
            )
        except Exception as exc:
            report_safe_exception(
                logger,
                event="recording_storage_failed",
                operation="upload_recording",
                error=exc,
                call_id=payload.get("call_id"),
                user_id=payload.get("user_id"),
                status="failed",
            )
            return RecordingResult(object_key=None, url=None, job_enqueued=False)
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
