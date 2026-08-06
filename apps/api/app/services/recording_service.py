from uuid import UUID

from fastapi import Request

from app.composition.runtime import get_api_runtime
from app.core.observability import bind_call_id
from app.providers.storage.base import StorageProvider


class RecordingService:
    def __init__(
        self,
        provider: StorageProvider,
    ) -> None:
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
            with bind_call_id(call_id):
                return await self.provider.get_download_url(
                    object_key=recording_object_key
                )
        except FileNotFoundError:
            return None

def get_recording_service(
    request: Request,
) -> RecordingService:
    return RecordingService(
        provider=get_api_runtime(request.app).storage_provider,
    )
