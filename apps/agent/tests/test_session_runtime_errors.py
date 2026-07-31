import asyncio
import logging
from uuid import uuid4

import pytest

from agent.api_client import TranscriptAppendPermanentError
from presvo_contracts import (
    CallCompletionAcknowledgement,
    CallCompletionRequest,
    CustomerCallDispatch,
    TranscriptAppendAcknowledgement,
    TranscriptSegment,
    create_contract,
)

from agent.session_runtime import SessionRuntime


class FakeEventPublisher:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def publish(self, payload: dict) -> None:
        self.events.append(payload)


class FailingEventPublisher:
    async def publish(self, payload: dict) -> None:
        raise RuntimeError("Redis connection refused")


class SecretBearingFailingEventPublisher:
    async def publish(self, payload: dict) -> None:
        raise RuntimeError("TRANSCRIPT_SENTINEL_FROM_PROVIDER_ERROR")


class FakeApiClient:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def complete_call(self, call_id, dispatch_token, request: CallCompletionRequest):
        self.calls.append((call_id, dispatch_token, request))
        return create_contract(
            CallCompletionAcknowledgement,
            status="accepted",
            queued=True,
            job_id=f"call-finalization:{call_id}",
        )

    async def append_transcript(
        self,
        _call_id: str,
        _dispatch_token: str,
        item: TranscriptSegment,
    ) -> TranscriptAppendAcknowledgement:
        return create_contract(
            TranscriptAppendAcknowledgement,
            status="stored",
            sequence_number=item.sequence_number,
        )


class FailingApiClient:
    async def complete_call(self, *_args) -> dict:
        raise RuntimeError("API unreachable")


class SecretBearingFailingApiClient:
    async def complete_call(self, *_args) -> dict:
        raise RuntimeError("AUTHORIZATION_SENTINEL_FROM_API_CLIENT")


class CloseFailingApiClient(FakeApiClient):
    async def aclose(self) -> None:
        raise RuntimeError("CLOSE_EXCEPTION_SENTINEL")


class PermanentlyFailingAppendClient(FakeApiClient):
    async def append_transcript(
        self,
        _call_id: str,
        _dispatch_token: str,
        _item: TranscriptSegment,
    ) -> dict:
        raise TranscriptAppendPermanentError("TRANSCRIPT_SENTINEL_FROM_APPEND")


def make_metadata(**kwargs) -> CustomerCallDispatch:
    call_id = kwargs.pop("call_id", uuid4())
    defaults = dict(
        job_type="customer_call",
        call_id=call_id,
        user_id=uuid4(),
        agent_config_id=uuid4(),
        agent_identity=f"agent-call-{call_id}",
        agent_name="A",
        owner_name="O",
        owner_context=None,
        system_prompt="Be helpful.",
        knowledge_base="Open weekdays.",
        pipeline_mode="stt_llm_tts",
        minutes_remaining=10,
        allowed_duration_seconds=600,
        dispatch_token="dispatch-token",
    )
    defaults.update(kwargs)
    return create_contract(CustomerCallDispatch, **defaults)


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
    assert call_ended["call_id"] == str(metadata.call_id)
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


@pytest.mark.anyio
async def test_transcript_publish_failure_does_not_log_provider_error_content(
    caplog,
) -> None:
    runtime = SessionRuntime(SecretBearingFailingEventPublisher())
    metadata = make_metadata()

    with caplog.at_level(logging.ERROR):
        await runtime.handle_caller_transcript(metadata, "caller transcript")

    assert "TRANSCRIPT_SENTINEL_FROM_PROVIDER_ERROR" not in caplog.text
    assert str(metadata.call_id) in caplog.text


@pytest.mark.anyio
async def test_agent_utterance_publish_failure_does_not_log_provider_error_content(
    caplog,
) -> None:
    runtime = SessionRuntime(SecretBearingFailingEventPublisher())
    metadata = make_metadata()

    with caplog.at_level(logging.ERROR):
        await runtime.handle_agent_utterance(metadata, "agent transcript")

    assert "TRANSCRIPT_SENTINEL_FROM_PROVIDER_ERROR" not in caplog.text
    assert str(metadata.call_id) in caplog.text


@pytest.mark.anyio
async def test_complete_call_failure_does_not_log_api_error_content(caplog) -> None:
    runtime = SessionRuntime(
        FakeEventPublisher(), api_client=SecretBearingFailingApiClient()
    )
    metadata = make_metadata(dispatch_token="dispatch_secret")

    with caplog.at_level(logging.ERROR):
        await runtime.finalize(metadata, duration_seconds=60)

    assert "AUTHORIZATION_SENTINEL_FROM_API_CLIENT" not in caplog.text
    assert str(metadata.call_id) in caplog.text


@pytest.mark.anyio
async def test_api_client_close_failure_does_not_break_finalize_or_leak_error(
    caplog,
) -> None:
    runtime = SessionRuntime(
        FakeEventPublisher(),
        api_client=CloseFailingApiClient(),
    )
    metadata = make_metadata(dispatch_token="CLOSE_TOKEN_SENTINEL")

    with caplog.at_level(logging.ERROR):
        await runtime.finalize(metadata, duration_seconds=60)

    assert runtime.pending_transcript == ()
    assert "CLOSE_EXCEPTION_SENTINEL" not in caplog.text
    assert "CLOSE_TOKEN_SENTINEL" not in caplog.text


@pytest.mark.anyio
async def test_call_ended_publish_failure_does_not_log_provider_error_content(
    caplog,
) -> None:
    runtime = SessionRuntime(SecretBearingFailingEventPublisher())
    metadata = make_metadata()

    with caplog.at_level(logging.ERROR):
        await runtime.finalize(metadata, duration_seconds=60)

    assert "TRANSCRIPT_SENTINEL_FROM_PROVIDER_ERROR" not in caplog.text
    assert str(metadata.call_id) in caplog.text


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
    assert api_client.calls[0][2].transcript == ()
    assert any(e["type"] == "call_ended" for e in publisher.events)


@pytest.mark.anyio
async def test_permanent_append_failure_keeps_item_requests_shutdown_and_hides_content(
    caplog: pytest.LogCaptureFixture,
) -> None:
    shutdown_reasons: list[str] = []
    runtime = SessionRuntime(
        FakeEventPublisher(),
        api_client=PermanentlyFailingAppendClient(),
        fatal_shutdown=lambda reason: shutdown_reasons.append(reason),
    )
    metadata = make_metadata(dispatch_token="DISPATCH_TOKEN_SENTINEL")

    with caplog.at_level(logging.ERROR):
        await runtime.handle_caller_transcript(metadata, "TRANSCRIPT_TEXT_SENTINEL")
        for _ in range(100):
            if shutdown_reasons:
                break
            await asyncio.sleep(0)

    assert shutdown_reasons == ["transcript_append_permanent_failure"]
    assert [item.sequence_number for item in runtime.pending_transcript] == [1]
    assert "TRANSCRIPT_SENTINEL_FROM_APPEND" not in caplog.text
    assert "TRANSCRIPT_TEXT_SENTINEL" not in caplog.text
    assert "DISPATCH_TOKEN_SENTINEL" not in caplog.text

    await runtime.finalize(metadata, duration_seconds=1)
