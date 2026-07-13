from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class LiveKitDispatch:
    id: str
    agent_name: str
    room: str
    metadata: str
    state: object | None = None


class LiveKitDispatchProvider(Protocol):
    async def list_dispatches(self, *, room_name: str) -> list[LiveKitDispatch]: ...

    async def create_dispatch(
        self,
        *,
        agent_name: str,
        room_name: str,
        metadata: str,
    ) -> LiveKitDispatch: ...
