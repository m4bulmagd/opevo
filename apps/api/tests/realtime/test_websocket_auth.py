import pytest
from uuid import UUID

from app.auth.domain import AuthenticatedUser
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


class FakeAuthenticator:
    async def authenticate(self, token: str) -> AuthenticatedUser:
        return AuthenticatedUser(
            internal_user_id=UUID("00000000-0000-0000-0000-000000000123")
        )


class FakeRedis:
    pass


@pytest.mark.anyio
async def test_websocket_requires_auth_message_before_events() -> None:
    websocket = FakeWebSocket()

    service = RealtimeService(
        authenticator=FakeAuthenticator(),
        event_bus=RedisEventBus(FakeRedis()),
        websocket_manager=WebSocketManager(),
        observability=FakeObservability(),
    )
    result = await service.authenticate(websocket)

    assert result is None
    assert websocket.sent_messages == [{"type": "error", "detail": "auth_required"}]
    assert websocket.closed_code == 1008
