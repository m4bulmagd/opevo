from livekit import api

from app.providers.livekit_recording.base import RecordingEgressResult
from app.providers.livekit_recording.base import RecordingProvider


class LiveKitRecordingProviderError(Exception):
    pass


class LiveKitRecordingProvider(RecordingProvider):
    _TERMINAL_STATUSES = frozenset(
        {
            api.EgressStatus.EGRESS_COMPLETE,
            api.EgressStatus.EGRESS_FAILED,
            api.EgressStatus.EGRESS_ABORTED,
            api.EgressStatus.EGRESS_LIMIT_REACHED,
        }
    )
    _STOPPABLE_STATUSES = frozenset(
        {
            api.EgressStatus.EGRESS_STARTING,
            api.EgressStatus.EGRESS_ACTIVE,
            api.EgressStatus.EGRESS_ENDING,
        }
    )
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

    def _is_aws_endpoint(self) -> bool:
        return "amazonaws.com" in self.endpoint_url

    def _build_s3_upload(self):
        upload = api.S3Upload(
            bucket=self.bucket_name,
            access_key=self.access_key,
            secret=self.secret_key,
            region=self.region,
            force_path_style=not self._is_aws_endpoint(),
        )
        if not self._is_aws_endpoint():
            upload.endpoint = self.endpoint_url
        return upload

    async def start_room_recording(self, *, room_name: str, user_id: str, call_id: str) -> RecordingEgressResult:
        object_key = f"calls/{user_id}/{call_id}.ogg"
        request = api.RoomCompositeEgressRequest(
            room_name=room_name,
            audio_only=True,
            file=api.EncodedFileOutput(
                filepath=object_key,
                s3=self._build_s3_upload(),
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

    async def stop_room_recording(self, *, egress_id: str) -> None:
        request = api.StopEgressRequest(egress_id=egress_id)
        try:
            await self.egress_client.stop_egress(request)
        except Exception as exc:  # pragma: no cover - exercised by tests via wrapping
            raise LiveKitRecordingProviderError(str(exc)) from exc

    async def ensure_stopped(self, egress_id: str) -> None:
        info = await self._get_egress(egress_id)
        if info is None or info.status in self._TERMINAL_STATUSES:
            return
        if info.status not in self._STOPPABLE_STATUSES:
            raise LiveKitRecordingProviderError("Egress status is not stoppable")

        await self.stop_room_recording(egress_id=egress_id)
        refreshed = await self._get_egress(egress_id)
        if refreshed is None or refreshed.status in self._TERMINAL_STATUSES:
            return
        raise LiveKitRecordingProviderError("Egress stop is not terminal")

    async def _get_egress(self, egress_id: str):
        try:
            response = await self.egress_client.list_egress(
                api.ListEgressRequest(egress_id=egress_id)
            )
        except Exception as exc:
            raise LiveKitRecordingProviderError(str(exc)) from exc
        matches = [
            item
            for item in response.items
            if getattr(item, "egress_id", None) == egress_id
        ]
        if not matches:
            return None
        if len(matches) != 1:
            raise LiveKitRecordingProviderError("Egress identity is ambiguous")
        return matches[0]
