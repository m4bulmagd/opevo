import asyncio
from collections.abc import Callable

import pytest
from uuid import uuid4

from agent.api_client import TranscriptAppendRetryableError
from agent.composition import build_agent_process_runtime
from agent.config import AgentSettings
from agent.event_publisher import EventPublisher
from presvo_contracts import (
    CALL_COMPLETION_TRANSCRIPT_MAX_ITEMS,
    CallCompletionAcknowledgement,
    CallCompletionRequest,
    CustomerCallDispatch,
    TranscriptAppendAcknowledgement,
    TranscriptSegment,
    create_contract,
    dump_contract,
    TranscriptObservedEvent,
)

from agent.session_runtime import (
    SessionRuntime,
    TranscriptBufferOverflow,
)


class FakeEventPublisher:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def publish(self, event: object) -> None:
        self.events.append(dump_contract(event))


class FakeApiClient:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.appends: list[TranscriptSegment] = []

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
        self.appends.append(item)
        return create_contract(
            TranscriptAppendAcknowledgement,
            status="stored",
            sequence_number=item.sequence_number,
        )


def make_metadata(**overrides) -> CustomerCallDispatch:
    call_id = overrides.pop("call_id", uuid4())
    defaults = {
        "call_id": call_id,
        "job_type": "customer_call",
        "user_id": uuid4(),
        "agent_config_id": uuid4(),
        "agent_identity": f"agent-call-{call_id}",
        "agent_name": "A",
        "owner_name": "O",
        "owner_context": None,
        "system_prompt": "Be helpful.",
        "knowledge_base": "Open weekdays.",
        "pipeline_mode": "stt_llm_tts",
        "minutes_remaining": 10,
        "allowed_duration_seconds": 600,
        "dispatch_token": "dispatch-token",
    }
    defaults.update(overrides)
    return create_contract(CustomerCallDispatch, **defaults)


@pytest.mark.anyio
async def test_session_runtime_publishes_transcript_events() -> None:
    fake_event_publisher = FakeEventPublisher()
    runtime = SessionRuntime(fake_event_publisher)

    metadata = make_metadata()
    await runtime.handle_agent_utterance(metadata, "Bonjour")

    assert fake_event_publisher.events[0]["type"] == "transcript_observed"
    assert fake_event_publisher.events[0]["user_id"] == str(metadata.user_id)


@pytest.mark.anyio
async def test_session_runtime_emits_call_end_event_and_flushes_transcript_to_api() -> (
    None
):
    fake_event_publisher = FakeEventPublisher()
    api_client = FakeApiClient()
    runtime = SessionRuntime(fake_event_publisher, api_client=api_client)

    dispatch_payload = make_metadata()
    await runtime.handle_agent_utterance(dispatch_payload, "Bonjour")
    await runtime.handle_caller_transcript(dispatch_payload, "What time do you open?")

    await runtime.finalize(
        dispatch_payload,
        duration_seconds=61,
    )

    assert fake_event_publisher.events[-1]["type"] == "agent_session_ended"
    assert fake_event_publisher.events[-1]["user_id"] == str(dispatch_payload.user_id)
    assert [item.model_dump() for item in api_client.appends] == [
        {"sequence_number": 1, "speaker": "AGENT", "text": "Bonjour"},
        {
            "sequence_number": 2,
            "speaker": "CALLER",
            "text": "What time do you open?",
        },
    ]
    assert api_client.calls == [
        (
            dispatch_payload.call_id,
            "dispatch-token",
            create_contract(CallCompletionRequest, duration_seconds=61, transcript=()),
        )
    ]


class FakeBus:
    def __init__(self) -> None:
        self.published: list[object] = []

    async def publish(self, event: object) -> None:
        self.published.append(event)


@pytest.mark.anyio
async def test_event_publisher_routes_events_to_user_channel() -> None:
    bus = FakeBus()
    publisher = EventPublisher(event_bus=bus)

    event = create_contract(
        TranscriptObservedEvent,
        type="transcript_observed",
        user_id=uuid4(),
        call_id=uuid4(),
        sequence_number=1,
        speaker="CALLER",
        text="Bonjour",
    )
    await publisher.publish(event)

    assert bus.published == [event]


async def wait_until(
    predicate: Callable[[], bool],
    *,
    attempts: int = 100,
) -> None:
    for _ in range(attempts):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError("condition was not reached")


class OrderedAppendClient(FakeApiClient):
    def __init__(self, *, blocked_sequence: int | None = None) -> None:
        super().__init__()
        self.appended: list[int] = []
        self.active_requests = 0
        self.maximum_active_requests = 0
        self.blocked_sequence = blocked_sequence
        self.release = asyncio.Event()

    async def append_transcript(
        self,
        _call_id: str,
        _dispatch_token: str,
        item: TranscriptSegment,
    ) -> TranscriptAppendAcknowledgement:
        self.appended.append(item.sequence_number)
        self.active_requests += 1
        self.maximum_active_requests = max(
            self.maximum_active_requests,
            self.active_requests,
        )
        try:
            if item.sequence_number == self.blocked_sequence:
                await self.release.wait()
            return create_contract(
                TranscriptAppendAcknowledgement,
                status="stored",
                sequence_number=item.sequence_number,
            )
        finally:
            self.active_requests -= 1


@pytest.mark.anyio
async def test_transcript_segments_are_sequenced_before_await_and_flushed_head_first() -> (
    None
):
    api_client = OrderedAppendClient(blocked_sequence=1)
    runtime = SessionRuntime(FakeEventPublisher(), api_client=api_client)
    metadata = make_metadata()

    await runtime.handle_caller_transcript(metadata, " first ")
    await runtime.handle_agent_utterance(metadata, "second")
    await wait_until(lambda: api_client.appended == [1])

    assert [item.sequence_number for item in runtime.transcript] == [1, 2]
    assert [item.text for item in runtime.transcript] == ["first", "second"]
    assert [item.sequence_number for item in runtime.pending_transcript] == [1, 2]

    api_client.release.set()
    await wait_until(lambda: not runtime.pending_transcript)

    assert api_client.appended == [1, 2]
    assert api_client.maximum_active_requests == 1
    await runtime.finalize(metadata, duration_seconds=2)


@pytest.mark.anyio
async def test_retryable_append_keeps_head_and_uses_capped_backoff() -> None:
    delays: list[float] = []

    async def record_sleep(delay: float) -> None:
        delays.append(delay)

    class RetryClient(FakeApiClient):
        def __init__(self) -> None:
            super().__init__()
            self.attempts = 0

        async def append_transcript(
            self,
            _call_id: str,
            _dispatch_token: str,
            item: TranscriptSegment,
        ) -> TranscriptAppendAcknowledgement:
            self.attempts += 1
            if self.attempts <= 5:
                raise TranscriptAppendRetryableError("retryable")
            return create_contract(
                TranscriptAppendAcknowledgement,
                status="duplicate",
                sequence_number=item.sequence_number,
            )

    client = RetryClient()
    runtime = SessionRuntime(
        FakeEventPublisher(),
        api_client=client,
        retry_sleep=record_sleep,
    )
    metadata = make_metadata()

    await runtime.handle_caller_transcript(metadata, "hello")
    await wait_until(lambda: not runtime.pending_transcript)

    assert client.attempts == 6
    assert delays == [1, 2, 4, 8, 10]
    await runtime.finalize(metadata, duration_seconds=1)


@pytest.mark.anyio
async def test_overflow_preserves_all_queued_items_and_requests_shutdown_once() -> None:
    shutdown_reasons: list[str] = []
    api_client = OrderedAppendClient(blocked_sequence=1)
    runtime = SessionRuntime(
        FakeEventPublisher(),
        api_client=api_client,
        fatal_shutdown=lambda reason: shutdown_reasons.append(reason),
    )
    metadata = make_metadata()

    for sequence in range(1, 201):
        await runtime.handle_caller_transcript(metadata, f"segment {sequence}")

    with pytest.raises(TranscriptBufferOverflow):
        await runtime.handle_caller_transcript(metadata, "overflow 201")
    with pytest.raises(TranscriptBufferOverflow):
        await runtime.handle_caller_transcript(metadata, "overflow 202")

    assert [item.sequence_number for item in runtime.pending_transcript] == list(
        range(1, 201)
    )
    assert len(runtime.transcript) == 200
    assert shutdown_reasons == ["transcript_buffer_overflow"]

    api_client.release.set()
    await wait_until(lambda: not runtime.pending_transcript, attempts=1000)
    await runtime.finalize(metadata, duration_seconds=1)


@pytest.mark.anyio
async def test_finalize_sends_only_unacknowledged_original_sequence_items() -> None:
    api_client = OrderedAppendClient(blocked_sequence=3)
    runtime = SessionRuntime(
        FakeEventPublisher(),
        api_client=api_client,
        finalize_timeout_seconds=0.01,
    )
    metadata = make_metadata()

    await runtime.handle_caller_transcript(metadata, "one")
    await runtime.handle_agent_utterance(metadata, "two")
    await runtime.handle_caller_transcript(metadata, "three")
    await wait_until(
        lambda: [item.sequence_number for item in runtime.pending_transcript] == [3]
    )

    await runtime.finalize(metadata, duration_seconds=3)

    assert api_client.calls[0][2].transcript == (
        TranscriptSegment(sequence_number=3, speaker="CALLER", text="three"),
    )
    assert runtime.pending_transcript == ()
    assert runtime.flusher_task is not None
    assert runtime.flusher_task.done()


@pytest.mark.anyio
async def test_finalize_retains_recovery_tail_until_completion_acknowledges() -> None:
    completion_started = asyncio.Event()
    completion_release = asyncio.Event()
    publisher = FakeEventPublisher()

    class CompletionBlockingClient(OrderedAppendClient):
        async def complete_call(self, call_id, dispatch_token, request):
            self.calls.append((call_id, dispatch_token, request))
            completion_started.set()
            await completion_release.wait()
            return create_contract(
                CallCompletionAcknowledgement,
                status="accepted", queued=True, job_id=f"call-finalization:{call_id}",
            )

    api_client = CompletionBlockingClient(blocked_sequence=1)
    runtime = SessionRuntime(
        publisher,
        api_client=api_client,
        finalize_timeout_seconds=0.01,
    )
    metadata = make_metadata()
    await runtime.handle_caller_transcript(metadata, "one")
    await wait_until(lambda: api_client.appended == [1])

    finalize_task = asyncio.create_task(runtime.finalize(metadata, duration_seconds=1))
    await completion_started.wait()

    assert [item.sequence_number for item in runtime.pending_transcript] == [1]
    await wait_until(
        lambda: any(event["type"] == "agent_session_ended" for event in publisher.events)
    )

    completion_release.set()
    await finalize_task
    assert runtime.pending_transcript == ()


@pytest.mark.anyio
async def test_finalize_closes_acceptance_but_drains_previously_registered_handlers() -> (
    None
):
    handler_started = asyncio.Event()
    handler_release = asyncio.Event()
    api_client = OrderedAppendClient()
    runtime = SessionRuntime(FakeEventPublisher(), api_client=api_client)
    metadata = make_metadata()

    async def delayed_handler() -> None:
        handler_started.set()
        await handler_release.wait()
        await runtime.handle_caller_transcript(metadata, "registered before close")

    assert runtime.create_handler_task(delayed_handler) is True
    await handler_started.wait()
    finalize_task = asyncio.create_task(runtime.finalize(metadata, duration_seconds=1))
    await wait_until(lambda: runtime.is_closing)

    assert (
        runtime.create_handler_task(
            lambda: runtime.handle_caller_transcript(metadata, "registered after close")
        )
        is False
    )
    assert (
        await runtime.handle_caller_transcript(metadata, "direct after close") is False
    )

    handler_release.set()
    await finalize_task

    assert [item.text for item in runtime.transcript] == ["registered before close"]
    assert api_client.appended == [1]


@pytest.mark.anyio
async def test_finalize_cancels_and_awaits_stuck_owned_handlers_within_one_budget() -> (
    None
):
    cancelled = asyncio.Event()
    runtime = SessionRuntime(
        FakeEventPublisher(),
        api_client=FakeApiClient(),
        finalize_timeout_seconds=0.01,
    )
    metadata = make_metadata()

    async def stuck_handler() -> None:
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    assert runtime.create_handler_task(stuck_handler) is True
    await asyncio.sleep(0)

    await asyncio.wait_for(
        runtime.finalize(metadata, duration_seconds=1),
        timeout=0.2,
    )

    assert cancelled.is_set()
    assert runtime.handler_tasks == ()


@pytest.mark.anyio
async def test_closing_runtime_rejects_duplicate_agent_callbacks_without_publishing() -> (
    None
):
    publisher = FakeEventPublisher()
    runtime = SessionRuntime(publisher, api_client=FakeApiClient())
    metadata = make_metadata()

    await runtime.handle_agent_utterance(metadata, "same")
    await runtime.finalize(metadata, duration_seconds=1)
    transcript_event_count = len(
        [event for event in publisher.events if event["type"] == "transcript_observed"]
    )

    assert await runtime.handle_agent_utterance(metadata, "same") is False
    assert (
        len([event for event in publisher.events if event["type"] == "transcript_observed"])
        == transcript_event_count
    )


@pytest.mark.anyio
async def test_rejected_handler_factory_constructs_no_nested_coroutines_or_warnings(
    recwarn: pytest.WarningsRecorder,
) -> None:
    runtime = SessionRuntime(FakeEventPublisher(), api_client=FakeApiClient())
    metadata = make_metadata()
    await runtime.finalize(metadata, duration_seconds=1)
    factory_calls = 0

    async def inner() -> None:
        return None

    async def outer(awaitable) -> None:
        await awaitable

    def factory():
        nonlocal factory_calls
        factory_calls += 1
        return outer(inner())

    assert runtime.create_handler_task(factory) is False
    await asyncio.sleep(0)

    assert factory_calls == 0
    assert not [
        warning for warning in recwarn if "never awaited" in str(warning.message)
    ]


@pytest.mark.anyio
async def test_failed_completion_ack_retains_recovery_and_second_finalize_retries_once() -> (
    None
):
    publisher = FakeEventPublisher()

    class FlakyCompletionClient(OrderedAppendClient):
        def __init__(self) -> None:
            super().__init__(blocked_sequence=1)
            self.completion_attempts = 0
            self.close_attempts = 0

        async def complete_call(self, call_id, dispatch_token, request):
            self.calls.append((call_id, dispatch_token, request))
            self.completion_attempts += 1
            if self.completion_attempts == 1:
                raise RuntimeError("completion acknowledgement rejected")
            return create_contract(
                CallCompletionAcknowledgement,
                status="accepted", queued=True, job_id=f"call-finalization:{call_id}",
            )

        async def aclose(self) -> None:
            self.close_attempts += 1

    api_client = FlakyCompletionClient()
    runtime = SessionRuntime(
        publisher,
        api_client=api_client,
        finalize_timeout_seconds=0.01,
    )
    metadata = make_metadata()
    await runtime.handle_caller_transcript(metadata, "recover me")
    await wait_until(lambda: api_client.appended == [1])

    await runtime.finalize(metadata, duration_seconds=1)

    assert [item.sequence_number for item in runtime.pending_transcript] == [1]
    assert api_client.completion_attempts == 1
    assert (
        len([event for event in publisher.events if event["type"] == "agent_session_ended"]) == 1
    )

    await runtime.finalize(metadata, duration_seconds=1)

    assert runtime.pending_transcript == ()
    assert api_client.completion_attempts == 2
    assert api_client.close_attempts == 0
    assert (
        len([event for event in publisher.events if event["type"] == "agent_session_ended"]) == 1
    )


@pytest.mark.anyio
async def test_process_runtime_closes_transports_once_after_session_finalize() -> None:
    class ClosingApiClient(FakeApiClient):
        def __init__(self) -> None:
            super().__init__()
            self.close_calls = 0

        async def aclose(self) -> None:
            self.close_calls += 1

    class FailingClosingPublisher(FakeEventPublisher):
        def __init__(self) -> None:
            super().__init__()
            self.close_calls = 0

        async def publish(self, _event: object) -> None:
            raise RuntimeError("Redis unavailable")

        async def aclose(self) -> None:
            self.close_calls += 1

    settings = AgentSettings()
    api_client = ClosingApiClient()
    publisher = FailingClosingPublisher()
    session_runtime = SessionRuntime(publisher, api_client=api_client)
    process_runtime = build_agent_process_runtime(
        settings,
        api_client_factory=lambda _settings: api_client,
        event_publisher_factory=lambda _settings: publisher,
    )
    metadata = make_metadata()

    await session_runtime.finalize(metadata, duration_seconds=5)

    assert api_client.close_calls == 0
    assert publisher.close_calls == 0

    await process_runtime.aclose()
    await process_runtime.aclose()

    assert api_client.close_calls == 1
    assert publisher.close_calls == 1


@pytest.mark.anyio
async def test_compatibility_transcript_is_bounded_without_dropping_old_items() -> None:
    assert CALL_COMPLETION_TRANSCRIPT_MAX_ITEMS == 2_000
    shutdown_reasons: list[str] = []
    api_client = FakeApiClient()
    runtime = SessionRuntime(
        FakeEventPublisher(),
        api_client=api_client,
        fatal_shutdown=lambda reason: shutdown_reasons.append(reason),
    )
    metadata = make_metadata()

    for sequence in range(1, CALL_COMPLETION_TRANSCRIPT_MAX_ITEMS + 1):
        await runtime.handle_caller_transcript(metadata, f"segment {sequence}")
        await wait_until(lambda: not runtime.pending_transcript)

    with pytest.raises(TranscriptBufferOverflow):
        await runtime.handle_caller_transcript(metadata, "one too many")

    assert len(runtime.transcript) == CALL_COMPLETION_TRANSCRIPT_MAX_ITEMS
    assert runtime.transcript[0].text == "segment 1"
    assert runtime.transcript[-1].text == (
        f"segment {CALL_COMPLETION_TRANSCRIPT_MAX_ITEMS}"
    )
    assert shutdown_reasons == ["transcript_history_overflow"]

    await runtime.finalize(metadata, duration_seconds=1)
