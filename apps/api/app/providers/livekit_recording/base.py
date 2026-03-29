from dataclasses import dataclass


@dataclass(frozen=True)
class RecordingEgressResult:
    egress_id: str
    object_key: str
    url: str | None


class RecordingProvider:
    async def start_room_recording(self, *, room_name: str, user_id: str, call_id: str) -> RecordingEgressResult:
        raise NotImplementedError

    async def stop_room_recording(self, *, egress_id: str) -> None:
        raise NotImplementedError
