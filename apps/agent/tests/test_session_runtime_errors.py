import pytest

from agent.schemas import DispatchMetadata
from agent.session_runtime import SessionRuntime


class FakeEventPublisher:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def publish(self, payload: dict) -> None:
        self.events.append(payload)


class FailingEventPublisher:
    async def publish(self, payload: dict) -> None:
        raise RuntimeError("Redis connection refused")


class FakeApiClient:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def complete_call(self, payload: dict) -> dict:
        self.calls.append(payload)
        return {"status": "accepted"}


class FailingApiClient:
    async def complete_call(self, payload: dict) -> dict:
        raise RuntimeError("API unreachable")


def make_metadata(**kwargs) -> DispatchMetadata:
    defaults = dict(call_id="call_123", user_id="user_123", agent_name="A", owner_name="O")
    defaults.update(kwargs)
    return DispatchMetadata(**defaults)


# T4-1: finalize() when api_client.complete_call() raises — should log but not crash
@pytest.mark.anyio
async def test_finalize_api_client_raises_does_not_crash() -> None:
    publisher = FakeEventPublisher()
    runtime = SessionRuntime(publisher, api_client=FailingApiClient())
    metadata = make_metadata()

    # Must not raise
    await runtime.finalize(metadata, duration_seconds=60)

    # call_ended event should still be published despite the API failure
    assert any(e["type"] == "call_ended" for e in publisher.events)


# T4-2: finalize() when event_publisher.publish() raises — should log but not crash
@pytest.mark.anyio
async def test_finalize_publisher_raises_does_not_crash() -> None:
    publisher = FailingEventPublisher()
    api_client = FakeApiClient()
    runtime = SessionRuntime(publisher, api_client=api_client)
    metadata = make_metadata()

    # Must not raise
    await runtime.finalize(metadata, duration_seconds=60)

    # API was still called
    assert len(api_client.calls) == 1


# T4-3: finalize() with api_client=None — should skip API call, still publish event
@pytest.mark.anyio
async def test_finalize_no_api_client_still_publishes_call_ended() -> None:
    publisher = FakeEventPublisher()
    runtime = SessionRuntime(publisher)  # api_client defaults to None
    metadata = make_metadata()

    await runtime.finalize(metadata, duration_seconds=45)

    assert any(e["type"] == "call_ended" for e in publisher.events)
    call_ended = next(e for e in publisher.events if e["type"] == "call_ended")
    assert call_ended["call_id"] == "call_123"
    assert call_ended["duration_seconds"] == 45


# T4-4: handle_caller_transcript when Redis publish fails — should log but not crash
@pytest.mark.anyio
async def test_handle_caller_transcript_publish_failure_does_not_crash() -> None:
    publisher = FailingEventPublisher()
    runtime = SessionRuntime(publisher)
    metadata = make_metadata()

    # Must not raise
    await runtime.handle_caller_transcript(metadata, "What are your opening hours?")

    # Transcript should still be recorded internally despite publish failure
    assert len(runtime.transcript) == 1
    assert runtime.transcript[0].speaker == "CALLER"
    assert runtime.transcript[0].text == "What are your opening hours?"


# T4-5: handle_agent_utterance deduplication — same utterance twice should only append once
@pytest.mark.anyio
async def test_handle_agent_utterance_deduplication() -> None:
    publisher = FakeEventPublisher()
    runtime = SessionRuntime(publisher)
    metadata = make_metadata()

    await runtime.handle_agent_utterance(metadata, "Hello, how can I help you?")
    await runtime.handle_agent_utterance(metadata, "Hello, how can I help you?")

    # Transcript should contain the utterance only once
    agent_lines = [item for item in runtime.transcript if item.speaker == "AGENT"]
    assert len(agent_lines) == 1
    assert agent_lines[0].text == "Hello, how can I help you?"

    # But the event is still published twice (dedup is only for internal transcript)
    agent_events = [e for e in publisher.events if e["speaker"] == "AGENT"]
    assert len(agent_events) == 2


# T4-6: finalize() with empty transcript
@pytest.mark.anyio
async def test_finalize_empty_transcript() -> None:
    publisher = FakeEventPublisher()
    api_client = FakeApiClient()
    runtime = SessionRuntime(publisher, api_client=api_client)
    metadata = make_metadata(minutes_remaining=5)

    await runtime.finalize(metadata, duration_seconds=30)

    assert len(api_client.calls) == 1
    assert api_client.calls[0]["transcript"] == []
    assert any(e["type"] == "call_ended" for e in publisher.events)
