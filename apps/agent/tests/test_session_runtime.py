import pytest

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

    await runtime.handle_agent_utterance({"call_id": "call_123"}, "Bonjour")

    assert fake_event_publisher.events[0]["type"] == "transcript"


@pytest.mark.anyio
async def test_session_runtime_emits_call_end_event() -> None:
    fake_event_publisher = FakeEventPublisher()
    runtime = SessionRuntime(fake_event_publisher)

    await runtime.finalize({"call_id": "call_123"}, duration_seconds=61)

    assert fake_event_publisher.events[-1]["type"] == "call_ended"
