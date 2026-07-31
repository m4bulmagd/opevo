import pytest

from app.core.auth import ClerkAuthProvider
from app.core.redis import RedisEventBus
from app.services.realtime_service import RealtimeService
from app.websockets.manager import WebSocketManager


class FakeWebSocket:
    def __init__(self) -> None:
        self.sent_messages: list[dict] = []
        self.closed_code: int | None = None

    async def receive_json(self) -> dict:
        return {"type": "ping"}

    async def send_json(self, payload: dict) -> None:
        self.sent_messages.append(payload)

    async def close(self, code: int) -> None:
        self.closed_code = code


class FakeObservability:
    def record_invalid_contract(self, **_attributes: str) -> None:
        pass


@pytest.mark.anyio
async def test_websocket_requires_auth_message_before_events() -> None:
    websocket = FakeWebSocket()

    service = RealtimeService(
        auth_provider=ClerkAuthProvider(),
        event_bus=RedisEventBus(),
        websocket_manager=WebSocketManager(),
        observability=FakeObservability(),
    )
    result = await service.authenticate(websocket)

    assert result is None
    assert websocket.sent_messages == [{"type": "error", "detail": "auth_required"}]
    assert websocket.closed_code == 1008
