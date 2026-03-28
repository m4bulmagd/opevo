from app.core.config import get_settings
from app.providers.livekit_recording.livekit import LiveKitRecordingProvider


class LiveKitRecordingService:
    def __init__(self, provider=None) -> None:
        self.provider = provider

    async def start_room_recording(self, *, room_name: str, user_id, call_id):
        if self.provider is not None:
            return await self.provider.start_room_recording(
                room_name=room_name,
                user_id=str(user_id),
                call_id=str(call_id),
            )

        from livekit import api

        settings = get_settings()
        if not settings.livekit_url or not settings.livekit_api_key or not settings.livekit_api_secret:
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
            return await provider.start_room_recording(
                room_name=room_name,
                user_id=str(user_id),
                call_id=str(call_id),
            )
        finally:
            await lkapi.aclose()
