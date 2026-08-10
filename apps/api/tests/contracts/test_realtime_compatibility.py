"""Compatibility checks for API-produced realtime wire events."""

import json
from pathlib import Path
from uuid import UUID

import pytest

from app.auth.domain import AuthenticatedUser
from app.core.redis import RedisEventBus
from app.services.realtime_service import RealtimeService
from app.websockets.manager import WebSocketManager
from opevo_contracts import realtime_channel


FIXTURES = (
    Path(__file__).resolve().parents[4]
    / "libs/shared/tests/fixtures/v1"
)
USER_ID = UUID("22222222-2222-4222-8222-222222222222")
CALL_ID = UUID("11111111-1111-4111-8111-111111111111")


class _Redis:
    def __init__(self) -> None:
        self.published: list[tuple[str, str]] = []

    async def publish(self, channel: str, payload: str) -> None:
        self.published.append((channel, payload))


class _Observability:
    def record_invalid_contract(self, **_attributes: str) -> None:
        pass


class _Authenticator:
    async def authenticate(self, token: str) -> AuthenticatedUser:
        return AuthenticatedUser(internal_user_id=USER_ID)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("method", "kwargs", "fixture"),
    [
        (
            "publish_call_started",
            {"room_name": "fixture-room-001", "call_id": CALL_ID},
            "call_started_event.json",
        ),
        (
            "publish_call_finalized",
            {
                "call_id": CALL_ID,
                "minutes_charged": 1,
                "summary_text": "Fixture call summary.",
            },
            "call_finalized_event.json",
        ),
    ],
)
async def test_api_realtime_producers_match_golden_contracts(
    method: str, kwargs: dict[str, object], fixture: str
) -> None:
    redis = _Redis()
    service = RealtimeService(
        authenticator=_Authenticator(),
        event_bus=RedisEventBus(redis_client=redis),
        websocket_manager=WebSocketManager(),
        observability=_Observability(),
    )

    await getattr(service, method)(USER_ID, **kwargs)

    expected_json = json.dumps(
        json.loads((FIXTURES / fixture).read_text()),
        separators=(",", ":"),
        sort_keys=True,
    )
    assert redis.published == [(realtime_channel(USER_ID), expected_json)]
