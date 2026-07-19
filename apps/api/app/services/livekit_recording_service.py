from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from app.core.config import get_settings
from app.providers.livekit_recording.base import (
    RecordingEgressResult,
    RecordingEgressSnapshot,
    RecordingProvider,
)
from app.providers.livekit_recording.livekit import LiveKitRecordingProvider


class LiveKitRecordingService:
    def __init__(self, provider: RecordingProvider | None = None) -> None:
        self.provider = provider

    async def start_room_recording(
        self,
        *,
        room_name: str,
        object_key: str,
    ) -> RecordingEgressResult:
        async with self._provider_session() as provider:
            return await provider.start_room_recording(
                room_name=room_name,
                object_key=object_key,
            )

    async def list_room_egresses(
        self,
        *,
        room_name: str,
    ) -> tuple[RecordingEgressSnapshot, ...]:
        async with self._provider_session() as provider:
            return await provider.list_room_egresses(room_name=room_name)

    async def stop_room_recording(self, *, egress_id: str) -> None:
        async with self._provider_session() as provider:
            await provider.stop_room_recording(egress_id=egress_id)

    async def ensure_stopped(self, egress_id: str) -> None:
        async with self._provider_session() as provider:
            await provider.ensure_stopped(egress_id)

    async def ensure_not_running(self, egress_id: str) -> None:
        async with self._provider_session() as provider:
            await provider.ensure_not_running(egress_id)

    @asynccontextmanager
    async def _provider_session(self) -> AsyncIterator[RecordingProvider]:
        if self.provider is not None:
            yield self.provider
            return

        from livekit import api

        settings = get_settings()
        if (
            not settings.livekit_url
            or not settings.livekit_api_key
            or not settings.livekit_api_secret
        ):
            raise ValueError("LiveKit settings are not configured")

        lkapi = api.LiveKitAPI(
            url=settings.livekit_url,
            api_key=settings.livekit_api_key,
            api_secret=settings.livekit_api_secret,
        )
        try:
            provider = LiveKitRecordingProvider(
                egress_client=lkapi.egress,
                bucket_name=settings.storage_bucket_name,
                endpoint_url=settings.s3_endpoint_url or "http://minio:9000",
                access_key=settings.s3_access_key,
                secret_key=settings.s3_secret_key,
                region=settings.s3_region,
            )
            yield provider
        finally:
            await lkapi.aclose()
