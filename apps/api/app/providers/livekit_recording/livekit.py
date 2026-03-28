from livekit import api

from app.providers.livekit_recording.base import RecordingEgressResult
from app.providers.livekit_recording.base import RecordingProvider


class LiveKitRecordingProviderError(Exception):
    pass


class LiveKitRecordingProvider(RecordingProvider):
    def __init__(
        self,
        *,
        egress_client,
        bucket_name: str,
        endpoint_url: str,
        access_key: str | None,
        secret_key: str | None,
        region: str,
    ) -> None:
        self.egress_client = egress_client
        self.bucket_name = bucket_name
        self.endpoint_url = endpoint_url.rstrip("/")
        self.access_key = access_key or ""
        self.secret_key = secret_key or ""
        self.region = region

    async def start_room_recording(self, *, room_name: str, user_id: str, call_id: str) -> RecordingEgressResult:
        object_key = f"calls/{user_id}/{call_id}.ogg"
        request = api.RoomCompositeEgressRequest(
            room_name=room_name,
            audio_only=True,
            file=api.EncodedFileOutput(
                filepath=object_key,
                s3=api.S3Upload(
                    bucket=self.bucket_name,
                    endpoint=self.endpoint_url,
                    access_key=self.access_key,
                    secret=self.secret_key,
                    region=self.region,
                    force_path_style=True,
                ),
            ),
        )
        try:
            info = await self.egress_client.start_room_composite_egress(request)
        except Exception as exc:  # pragma: no cover - exercised by tests via wrapping
            raise LiveKitRecordingProviderError(str(exc)) from exc

        return RecordingEgressResult(
            egress_id=info.egress_id,
            object_key=object_key,
            url=f"{self.endpoint_url}/{self.bucket_name}/{object_key}",
        )
