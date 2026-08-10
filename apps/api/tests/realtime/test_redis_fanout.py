import json
from uuid import UUID

import pytest

from app.auth.domain import AuthenticatedUser
from app.core.redis import RedisEventBus
from app.services.realtime_service import RealtimeService
from app.websockets.manager import WebSocketManager
from opevo_contracts import (
    AgentSessionEndedEvent,
    CallFinalizedEvent,
    CallStartedEvent,
    TranscriptObservedEvent,
    create_contract,
    dump_contract,
)


USER_ID = UUID("22222222-2222-4222-8222-222222222222")
OTHER_USER_ID = UUID("33333333-3333-4333-8333-333333333333")
CALL_ID = UUID("11111111-1111-4111-8111-111111111111")


class FakeEventBus:
    def __init__(self, events: list[tuple[str, object]] | None = None) -> None:
        self.published: list[object] = []
        self._events = events or []

    async def publish(self, event: object) -> None:
        self.published.append(event)

    async def subscribe(self):
        for event in self._events:
            yield event


class FakeObservability:
    def __init__(self) -> None:
        self.invalid: list[dict[str, str]] = []

    def record_invalid_contract(self, **attributes: str) -> None:
        self.invalid.append(attributes)


class FakeWebSocket:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send_json(self, payload: dict) -> None:
        self.messages.append(payload)


class FakeAuthenticator:
    async def authenticate(self, token: str) -> AuthenticatedUser:
        return AuthenticatedUser(internal_user_id=USER_ID)


class _PubSub:
    def __init__(self, messages: list[dict[str, object]]) -> None:
        self.messages = messages

    async def psubscribe(self, _pattern: str) -> None:
        pass

    async def listen(self):
        for message in self.messages:
            yield message

    async def aclose(self) -> None:
        pass


class _Redis:
    def __init__(self, messages: list[dict[str, object]]) -> None:
        self.messages = messages

    def pubsub(self) -> _PubSub:
        return _PubSub(self.messages)


def _event() -> CallStartedEvent:
    return create_contract(
        CallStartedEvent,
        type="call_started",
        user_id=USER_ID,
        call_id=CALL_ID,
        room_name="room-1",
    )


def _all_events() -> list[object]:
    return [
        _event(),
        create_contract(
            CallFinalizedEvent,
            type="call_finalized",
            user_id=USER_ID,
            call_id=CALL_ID,
            minutes_charged=1,
            summary_text=None,
        ),
        create_contract(
            TranscriptObservedEvent,
            type="transcript_observed",
            user_id=USER_ID,
            call_id=CALL_ID,
            sequence_number=1,
            speaker="CALLER",
            text="Bonjour",
        ),
        create_contract(
            AgentSessionEndedEvent,
            type="agent_session_ended",
            user_id=USER_ID,
            call_id=CALL_ID,
            duration_seconds=42,
        ),
    ]


def _service(
    event_bus: FakeEventBus, manager: WebSocketManager | None = None
) -> tuple[RealtimeService, FakeObservability]:
    observability = FakeObservability()
    return (
        RealtimeService(
            authenticator=FakeAuthenticator(),
            event_bus=event_bus,
            websocket_manager=manager or WebSocketManager(),
            observability=observability,
        ),
        observability,
    )


@pytest.mark.anyio
async def test_realtime_service_publishes_call_started_as_typed_event() -> None:
    event_bus = FakeEventBus()
    service, _ = _service(event_bus)

    await service.publish_call_started(USER_ID, room_name="room-1", call_id=CALL_ID)

    assert event_bus.published == [_event()]


@pytest.mark.anyio
async def test_redis_subscription_canonicalizes_channel_and_leaves_payload_raw() -> None:
    bus = RedisEventBus(
        redis_client=_Redis(
            [
                {
                    "type": "pmessage",
                    "channel": f"realtime:user:{USER_ID}",
                    "data": "raw",
                }
            ]
        )
    )

    event = await anext(bus.subscribe())

    assert event == (str(USER_ID), "raw")


@pytest.mark.anyio
@pytest.mark.parametrize("empty_payload", ["", b""])
async def test_actual_redis_adapter_validates_empty_payload_then_broadcasts_next_valid(
    empty_payload: str | bytes,
) -> None:
    manager = WebSocketManager()
    websocket = FakeWebSocket()
    await manager.connect(str(USER_ID), websocket)
    valid_payload = json.dumps(dump_contract(_event()))
    redis = _Redis(
        [
            {
                "type": "pmessage",
                "channel": f"realtime:user:{USER_ID}",
                "data": empty_payload,
            },
            {
                "type": "pmessage",
                "channel": f"realtime:user:{USER_ID}",
                "data": valid_payload,
            },
        ]
    )
    service, observability = _service(RedisEventBus(redis_client=redis), manager)

    await service.fanout_once()

    assert websocket.messages == [dump_contract(_event())]
    assert observability.invalid == [
        {
            "contract_name": "RealtimeEvent",
            "code": "malformed_json",
            "transport": "redis",
        }
    ]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("invalid_channel", "secret"),
    [
        (b"realtime:user:\xff", "realtime:user"),
        (12345, "12345"),
        (f"wrong:user:{USER_ID}", str(USER_ID)),
        ("realtime:user:INVALID_CHANNEL_SENTINEL", "INVALID_CHANNEL_SENTINEL"),
        (f"realtime:user:{USER_ID.hex}", USER_ID.hex),
    ],
)
async def test_actual_redis_adapter_silently_discards_invalid_channel_then_continues(
    invalid_channel: object,
    secret: str | None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    manager = WebSocketManager()
    websocket = FakeWebSocket()
    await manager.connect(str(USER_ID), websocket)
    invalid_event = create_contract(
        CallStartedEvent,
        type="call_started",
        user_id=USER_ID,
        call_id=CALL_ID,
        room_name="invalid-channel-event",
    )
    redis = _Redis(
        [
            {
                "type": "pmessage",
                "channel": invalid_channel,
                "data": json.dumps(dump_contract(invalid_event)),
            },
            {
                "type": "pmessage",
                "channel": f"realtime:user:{USER_ID}",
                "data": json.dumps(dump_contract(_event())),
            },
        ]
    )
    service, observability = _service(RedisEventBus(redis_client=redis), manager)

    await service.fanout_once()

    assert websocket.messages == [dump_contract(_event())]
    assert observability.invalid == []
    assert "invalid-channel-event" not in caplog.text
    if secret is not None:
        assert secret not in caplog.text


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("raw_payload", "code"),
    [
        ("{not-json", "malformed_json"),
        (json.dumps({"type": "call_started"}), "missing_schema_version"),
        (
            json.dumps({"schema_version": 2, "type": "call_started"}),
            "unsupported_schema_version",
        ),
        (
            json.dumps({"schema_version": 1, "type": "unknown"}),
            "invalid_payload",
        ),
        (json.dumps(["not-an-object"]), "invalid_payload"),
    ],
)
async def test_fanout_once_discards_invalid_message_then_broadcasts_next_valid(
    raw_payload: str, code: str
) -> None:
    manager = WebSocketManager()
    websocket = FakeWebSocket()
    await manager.connect(str(USER_ID), websocket)
    bus = FakeEventBus(
        [(str(USER_ID), raw_payload), (str(USER_ID), json.dumps(dump_contract(_event())))]
    )
    service, observability = _service(bus, manager)

    await service.fanout_once()

    assert websocket.messages == [dump_contract(_event())]
    assert observability.invalid == [
        {"contract_name": "RealtimeEvent", "code": code, "transport": "redis"}
    ]


@pytest.mark.anyio
async def test_fanout_discards_channel_mismatch_without_identifier_disclosure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    manager = WebSocketManager()
    websocket = FakeWebSocket()
    await manager.connect(str(USER_ID), websocket)
    bus = FakeEventBus(
        [
            (str(OTHER_USER_ID), json.dumps(dump_contract(_event()))),
            (str(USER_ID), json.dumps(dump_contract(_event()))),
        ]
    )
    service, observability = _service(bus, manager)

    await service.fanout_once()

    assert websocket.messages == [dump_contract(_event())]
    assert observability.invalid == [
        {
            "contract_name": "CallStartedEvent",
            "code": "channel_user_mismatch",
            "transport": "redis",
        }
    ]
    assert str(USER_ID) not in caplog.text
    assert str(OTHER_USER_ID) not in caplog.text
    assert "realtime_event_rejected code=channel_user_mismatch transport=redis" in caplog.text


@pytest.mark.anyio
async def test_fanout_accepts_additive_fields_and_discards_them() -> None:
    manager = WebSocketManager()
    websocket = FakeWebSocket()
    await manager.connect(str(USER_ID), websocket)
    payload = dump_contract(_event()) | {"future_field": "ignored"}
    service, observability = _service(
        FakeEventBus([(str(USER_ID), json.dumps(payload))]), manager
    )

    await service.fanout_once()

    assert websocket.messages == [dump_contract(_event())]
    assert observability.invalid == []


@pytest.mark.anyio
async def test_fanout_forever_broadcasts_every_supported_event_variant() -> None:
    manager = WebSocketManager()
    websocket = FakeWebSocket()
    await manager.connect(str(USER_ID), websocket)
    events = _all_events()
    service, observability = _service(
        FakeEventBus(
            [(str(USER_ID), json.dumps(dump_contract(event))) for event in events]
        ),
        manager,
    )

    await service.fanout_forever()

    assert websocket.messages == [dump_contract(event) for event in events]
    assert observability.invalid == []
