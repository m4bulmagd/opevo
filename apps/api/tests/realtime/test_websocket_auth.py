import pytest

from app.services.realtime_service import RealtimeService


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


@pytest.mark.anyio
async def test_websocket_requires_auth_message_before_events() -> None:
    websocket = FakeWebSocket()

    result = await RealtimeService().authenticate(websocket)

    assert result is None
    assert websocket.sent_messages == [{"type": "error", "detail": "auth_required"}]
    assert websocket.closed_code == 1008
