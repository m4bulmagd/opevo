from app.providers.livekit_recording.base import (
    RecordingEgressResult,
    RecordingEgressSnapshot,
    RecordingProvider,
)


class LiveKitRecordingService:
    def __init__(self, provider: RecordingProvider) -> None:
        self.provider = provider

    async def start_room_recording(
        self,
        *,
        room_name: str,
        object_key: str,
    ) -> RecordingEgressResult:
        return await self.provider.start_room_recording(
            room_name=room_name,
            object_key=object_key,
        )

    async def list_room_egresses(
        self,
        *,
        room_name: str,
    ) -> tuple[RecordingEgressSnapshot, ...]:
        return await self.provider.list_room_egresses(room_name=room_name)

    async def stop_room_recording(self, *, egress_id: str) -> None:
        await self.provider.stop_room_recording(egress_id=egress_id)

    async def ensure_stopped(self, egress_id: str) -> None:
        await self.provider.ensure_stopped(egress_id)

    async def ensure_not_running(self, egress_id: str) -> None:
        await self.provider.ensure_not_running(egress_id)
