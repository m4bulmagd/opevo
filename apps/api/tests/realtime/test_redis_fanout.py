import pytest

from app.core.auth import ClerkAuthProvider
from app.services.realtime_service import RealtimeService
from app.websockets.manager import WebSocketManager


class FakeEventBus:
    def __init__(self, events: list[tuple[str, dict]] | None = None) -> None:
        self.published: list[tuple[str, dict]] = []
        self._events = events or []

    async def publish_json(self, user_id: str, payload: dict) -> None:
        self.published.append((user_id, payload))

    async def subscribe(self):
        for event in self._events:
            yield event


class FakeWebSocket:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send_json(self, payload: dict) -> None:
        self.messages.append(payload)


@pytest.mark.anyio
async def test_realtime_service_publishes_call_started_into_event_bus() -> None:
    event_bus = FakeEventBus()
    service = RealtimeService(
        auth_provider=ClerkAuthProvider(),
        event_bus=event_bus,
        websocket_manager=WebSocketManager(),
    )

    await service.publish_call_started("user_123", room_name="room-1", call_id="call-1")

    assert event_bus.published == [
        (
            "user_123",
            {"type": "call_started", "room_name": "room-1", "call_id": "call-1"},
        )
    ]


@pytest.mark.anyio
async def test_realtime_service_relays_bus_events_to_connected_websockets() -> None:
    manager = WebSocketManager()
    websocket = FakeWebSocket()
    await manager.connect("user_123", websocket)

    event_bus = FakeEventBus(
        events=[
            (
                "user_123",
                {"type": "transcript", "call_id": "call-1", "speaker": "AGENT", "text": "Bonjour"},
            )
        ]
    )
    service = RealtimeService(
        auth_provider=ClerkAuthProvider(),
        event_bus=event_bus,
        websocket_manager=manager,
    )

    await service.fanout_once()

    assert websocket.messages == [
        {"type": "transcript", "call_id": "call-1", "speaker": "AGENT", "text": "Bonjour"}
    ]
