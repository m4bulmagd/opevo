from typing import Protocol
from uuid import UUID

from fastapi import Depends

from app.core.observability import bind_call_id
from app.providers.storage.base import StorageProvider, StorageProviderError
from app.providers.storage.s3 import S3Storage, get_s3_storage
from app.services.livekit_recording_service import LiveKitRecordingService


class RecordingDeleteRetryableError(Exception):
    pass


class RecordingEgressStopper(Protocol):
    async def ensure_not_running(self, egress_id: str) -> None: ...


class RecordingService:
    def __init__(
        self,
        provider: StorageProvider,
        *,
        egress_stopper: RecordingEgressStopper | None = None,
    ) -> None:
        self.provider = provider
        self.egress_stopper = egress_stopper

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
            with bind_call_id(call_id):
                return await self.provider.get_download_url(
                    object_key=recording_object_key
                )
        except FileNotFoundError:
            return None

    async def delete_recording(
        self,
        *,
        call_id: UUID,
        recording_object_key: str | None,
        recording_egress_id: str | None = None,
    ) -> None:
        with bind_call_id(call_id):
            if recording_egress_id:
                stopper = self.egress_stopper or LiveKitRecordingService()
                try:
                    await stopper.ensure_not_running(recording_egress_id)
                except Exception:
                    raise RecordingDeleteRetryableError from None
            if not recording_object_key:
                return
            try:
                await self.provider.delete_object(object_key=recording_object_key)
            except FileNotFoundError:
                return
            except StorageProviderError:
                raise RecordingDeleteRetryableError from None


def get_recording_service(
    storage_provider: S3Storage = Depends(get_s3_storage),
) -> RecordingService:
    return RecordingService(provider=storage_provider)
