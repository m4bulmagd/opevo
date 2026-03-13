import pytest

from agent.event_publisher import EventPublisher
from agent.session_runtime import SessionRuntime


class FakeEventPublisher:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def publish(self, payload: dict) -> None:
        self.events.append(payload)


@pytest.mark.anyio
async def test_session_runtime_publishes_transcript_events() -> None:
    fake_event_publisher = FakeEventPublisher()
    runtime = SessionRuntime(fake_event_publisher)

    await runtime.handle_agent_utterance({"call_id": "call_123", "user_id": "user_123"}, "Bonjour")

    assert fake_event_publisher.events[0]["type"] == "transcript"
    assert fake_event_publisher.events[0]["user_id"] == "user_123"


@pytest.mark.anyio
async def test_session_runtime_emits_call_end_event() -> None:
    fake_event_publisher = FakeEventPublisher()
    runtime = SessionRuntime(fake_event_publisher)

    await runtime.finalize({"call_id": "call_123", "user_id": "user_123"}, duration_seconds=61)

    assert fake_event_publisher.events[-1]["type"] == "call_ended"
    assert fake_event_publisher.events[-1]["user_id"] == "user_123"


class FakeBus:
    def __init__(self) -> None:
        self.published: list[tuple[str, dict]] = []

    async def publish_json(self, user_id: str, payload: dict) -> None:
        self.published.append((user_id, payload))


@pytest.mark.anyio
async def test_event_publisher_routes_events_to_user_channel() -> None:
    bus = FakeBus()
    publisher = EventPublisher(event_bus=bus)

    await publisher.publish({"user_id": "user_123", "type": "transcript", "text": "Bonjour"})

    assert bus.published == [
        ("user_123", {"user_id": "user_123", "type": "transcript", "text": "Bonjour"})
    ]
