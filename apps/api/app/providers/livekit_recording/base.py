from dataclasses import dataclass
from typing import Literal
from uuid import UUID


StartOutcome = Literal["not_started", "unknown"]


@dataclass(frozen=True)
class RecordingEgressResult:
    egress_id: str
    object_key: str
    url: str | None


@dataclass(frozen=True)
class RecordingEgressSnapshot:
    egress_id: str
    room_name: str
    status: int
    object_key: str | None


def build_recording_object_key(
    *,
    user_id: UUID | str,
    call_id: UUID | str,
) -> str:
    return f"calls/{user_id}/{call_id}.ogg"


class RecordingProvider:
    async def start_room_recording(
        self,
        *,
        room_name: str,
        object_key: str,
    ) -> RecordingEgressResult:
        raise NotImplementedError

    async def list_room_egresses(
        self,
        *,
        room_name: str,
    ) -> tuple[RecordingEgressSnapshot, ...]:
        raise NotImplementedError

    async def stop_room_recording(self, *, egress_id: str) -> None:
        raise NotImplementedError

    async def ensure_stopped(self, egress_id: str) -> None:
        raise NotImplementedError

    async def ensure_not_running(self, egress_id: str) -> None:
        raise NotImplementedError
