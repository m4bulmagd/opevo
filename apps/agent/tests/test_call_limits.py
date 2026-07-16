import asyncio
import logging
from uuid import uuid4

import pytest
from pydantic import ValidationError

from agent.schemas import DispatchMetadata
from agent import schemas as schema_module
from agent.session_runtime import (
    CALL_LIMIT_EXPIRY_MESSAGE,
    CALL_LIMIT_WARNING_MESSAGE,
    SessionRuntime,
)


WARNING_MESSAGE = "You have one minute remaining in this call."


def test_call_limit_messages_use_approved_english_copy() -> None:
    assert CALL_LIMIT_WARNING_MESSAGE == WARNING_MESSAGE
    assert CALL_LIMIT_EXPIRY_MESSAGE == (
        "The maximum call duration has been reached. "
        "Thank you for calling. Goodbye."
    )


class FakeEventPublisher:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def publish(self, payload: dict) -> None:
        self.events.append(payload)


class AdvancingClock:
    def __init__(self) -> None:
        self.current = 0.0
        self.sleep_calls: list[float] = []

    def now(self) -> float:
        return self.current

    async def sleep(self, delay: float) -> None:
        self.sleep_calls.append(delay)
        self.current += delay


class BlockingSleeper:
    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def __call__(self, _delay: float) -> None:
        self.started.set()
        await asyncio.Event().wait()


def make_metadata(**overrides) -> DispatchMetadata:
    call_id = overrides.pop("call_id", str(uuid4()))
    defaults = {
        "call_id": call_id,
        "user_id": str(uuid4()),
        "agent_config_id": str(uuid4()),
        "agent_identity": f"agent-call-{call_id}",
        "agent_name": "Agent",
        "owner_name": "Owner",
        "owner_context": None,
        "system_prompt": "Be helpful.",
        "knowledge_base": "Open weekdays.",
        "pipeline_mode": "stt_llm_tts",
        "minutes_remaining": 10,
        "allowed_duration_seconds": 120,
        "dispatch_token": "dispatch-token",
    }
    defaults.update(overrides)
    return DispatchMetadata(**defaults)


def test_dispatch_metadata_requires_a_positive_allowed_duration() -> None:
    with pytest.raises(ValidationError):
        make_metadata(allowed_duration_seconds=0)

    payload = make_metadata().model_dump()
    payload.pop("allowed_duration_seconds")

    with pytest.raises(ValidationError):
        DispatchMetadata.model_validate(payload)


@pytest.mark.parametrize(
    ("field_name", "constant_name", "expected_maximum"),
    [
        ("agent_name", "AGENT_NAME_MAX_LENGTH", 80),
        ("owner_name", "OWNER_NAME_MAX_LENGTH", 255),
        ("owner_context", "OWNER_CONTEXT_MAX_LENGTH", 4_000),
        ("system_prompt", "SYSTEM_PROMPT_MAX_LENGTH", 8_000),
        ("knowledge_base", "KNOWLEDGE_BASE_MAX_LENGTH", 32_000),
    ],
)
def test_agent_dispatch_metadata_normalizes_and_bounds_customer_content(
    field_name: str,
    constant_name: str,
    expected_maximum: int,
) -> None:
    assert getattr(schema_module, constant_name) == expected_maximum
    bounded_value = "x" * expected_maximum
    metadata = make_metadata(**{field_name: f"  {bounded_value}  "})

    assert getattr(metadata, field_name) == bounded_value
    with pytest.raises(ValidationError):
        make_metadata(**{field_name: "x" * (expected_maximum + 1)})


def test_agent_dispatch_metadata_rejects_unknown_pipeline_mode() -> None:
    with pytest.raises(ValidationError):
        make_metadata(pipeline_mode="custom")


@pytest.mark.anyio
async def test_call_limit_warns_at_sixty_seconds_and_preserves_expiry_deadline() -> None:
    clock = AdvancingClock()
    warnings: list[tuple[float, str]] = []
    disconnect_times: list[float] = []

    async def warn(message: str) -> None:
        warnings.append((clock.now(), message))
        clock.current += 7

    async def disconnect() -> None:
        disconnect_times.append(clock.now())

    runtime = SessionRuntime(
        FakeEventPublisher(),
        warning_callback=warn,
        call_limit_clock=clock.now,
        call_limit_sleep=clock.sleep,
    )

    runtime.enforce_call_limit(make_metadata(), disconnect)
    assert runtime.call_limit_expired_on_start is False
    await runtime.call_limit_task

    assert warnings == [(60.0, WARNING_MESSAGE)]
    assert clock.sleep_calls == [60.0, 53.0]
    assert disconnect_times == [120.0]


@pytest.mark.anyio
async def test_setup_time_is_deducted_from_warning_and_expiry_deadlines() -> None:
    clock = AdvancingClock()
    clock.current = 7.0
    warnings: list[tuple[float, str]] = []
    disconnect_times: list[float] = []

    async def warn(message: str) -> None:
        warnings.append((clock.now(), message))

    async def disconnect() -> None:
        disconnect_times.append(clock.now())

    runtime = SessionRuntime(
        FakeEventPublisher(),
        warning_callback=warn,
        call_limit_started_at=0.0,
        call_limit_clock=clock.now,
        call_limit_sleep=clock.sleep,
    )

    runtime.enforce_call_limit(make_metadata(), disconnect)
    assert runtime.call_limit_expired_on_start is False
    await runtime.call_limit_task

    assert warnings == [(60.0, WARNING_MESSAGE)]
    assert clock.sleep_calls == [53.0, 60.0]
    assert disconnect_times == [120.0]


@pytest.mark.anyio
async def test_expired_allowance_disconnects_immediately_after_wiring() -> None:
    clock = AdvancingClock()
    clock.current = 121.0
    warnings: list[str] = []
    disconnect_times: list[float] = []

    async def warn(message: str) -> None:
        warnings.append(message)

    async def disconnect() -> None:
        disconnect_times.append(clock.now())

    runtime = SessionRuntime(
        FakeEventPublisher(),
        warning_callback=warn,
        call_limit_started_at=0.0,
        call_limit_clock=clock.now,
        call_limit_sleep=clock.sleep,
    )

    runtime.enforce_call_limit(make_metadata(), disconnect)
    assert runtime.call_limit_expired_on_start is True
    await runtime.call_limit_task

    assert warnings == []
    assert clock.sleep_calls == []
    assert disconnect_times == [121.0]


@pytest.mark.anyio
async def test_missed_warning_point_is_skipped_without_extending_expiry() -> None:
    clock = AdvancingClock()
    clock.current = 70.0
    warnings: list[str] = []
    disconnect_times: list[float] = []

    async def warn(message: str) -> None:
        warnings.append(message)

    async def disconnect() -> None:
        disconnect_times.append(clock.now())

    runtime = SessionRuntime(
        FakeEventPublisher(),
        warning_callback=warn,
        call_limit_started_at=0.0,
        call_limit_clock=clock.now,
        call_limit_sleep=clock.sleep,
    )

    runtime.enforce_call_limit(make_metadata(), disconnect)
    await runtime.call_limit_task

    assert warnings == []
    assert clock.sleep_calls == [50.0]
    assert disconnect_times == [120.0]


@pytest.mark.anyio
async def test_delayed_warning_wake_past_deadline_disconnects_without_warning() -> None:
    clock = AdvancingClock()
    warnings: list[str] = []
    disconnect_times: list[float] = []

    async def overshooting_sleep(delay: float) -> None:
        clock.sleep_calls.append(delay)
        clock.current += delay + 61

    async def warn(message: str) -> None:
        warnings.append(message)

    async def disconnect() -> None:
        disconnect_times.append(clock.now())

    runtime = SessionRuntime(
        FakeEventPublisher(),
        warning_callback=warn,
        call_limit_clock=clock.now,
        call_limit_sleep=overshooting_sleep,
    )

    runtime.enforce_call_limit(make_metadata(), disconnect)
    await runtime.call_limit_task

    assert warnings == []
    assert clock.sleep_calls == [60.0]
    assert disconnect_times == [121.0]


@pytest.mark.anyio
async def test_call_limit_does_not_warn_when_allowance_is_exactly_ninety_seconds() -> None:
    clock = AdvancingClock()
    warnings: list[str] = []
    disconnect_times: list[float] = []

    async def warn(message: str) -> None:
        warnings.append(message)

    async def disconnect() -> None:
        disconnect_times.append(clock.now())

    runtime = SessionRuntime(
        FakeEventPublisher(),
        warning_callback=warn,
        call_limit_clock=clock.now,
        call_limit_sleep=clock.sleep,
    )

    runtime.enforce_call_limit(
        make_metadata(allowed_duration_seconds=90),
        disconnect,
    )
    await runtime.call_limit_task

    assert warnings == []
    assert clock.sleep_calls == [90.0]
    assert disconnect_times == [90.0]


@pytest.mark.anyio
async def test_finalize_cancels_and_awaits_call_limit_timer() -> None:
    sleeper = BlockingSleeper()
    disconnects: list[bool] = []

    async def disconnect() -> None:
        disconnects.append(True)

    runtime = SessionRuntime(
        FakeEventPublisher(),
        call_limit_sleep=sleeper,
    )
    metadata = make_metadata()
    runtime.enforce_call_limit(metadata, disconnect)
    timer = runtime.call_limit_task
    await sleeper.started.wait()

    await runtime.finalize(metadata, duration_seconds=1)

    assert timer.done()
    assert timer.cancelled()
    assert runtime.call_limit_task is None
    assert disconnects == []


@pytest.mark.anyio
async def test_finalize_called_from_expiry_timer_never_awaits_itself() -> None:
    clock = AdvancingClock()
    runtime = SessionRuntime(
        FakeEventPublisher(),
        call_limit_clock=clock.now,
        call_limit_sleep=clock.sleep,
    )
    metadata = make_metadata(allowed_duration_seconds=60)

    async def disconnect() -> None:
        await runtime.finalize(metadata, duration_seconds=60)

    runtime.enforce_call_limit(metadata, disconnect)
    timer = runtime.call_limit_task
    await timer

    assert timer.done()
    assert not timer.cancelled()
    assert runtime.call_limit_task is None


@pytest.mark.anyio
async def test_warning_failure_is_redacted_and_does_not_extend_or_cancel_limit(
    caplog: pytest.LogCaptureFixture,
) -> None:
    clock = AdvancingClock()
    disconnect_times: list[float] = []
    metadata = make_metadata(
        call_id="CALL_METADATA_SENTINEL",
        dispatch_token="DISPATCH_TOKEN_SENTINEL",
    )

    async def warn(_message: str) -> None:
        raise RuntimeError("WARNING_PROVIDER_SENTINEL")

    async def disconnect() -> None:
        disconnect_times.append(clock.now())

    runtime = SessionRuntime(
        FakeEventPublisher(),
        warning_callback=warn,
        call_limit_clock=clock.now,
        call_limit_sleep=clock.sleep,
    )

    with caplog.at_level(logging.WARNING):
        runtime.enforce_call_limit(metadata, disconnect)
        await runtime.call_limit_task

    assert disconnect_times == [120.0]
    assert "call limit warning failed" in caplog.text
    assert "error_type=RuntimeError" in caplog.text
    assert "WARNING_PROVIDER_SENTINEL" not in caplog.text
    assert metadata.call_id not in caplog.text
    assert metadata.dispatch_token not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


@pytest.mark.anyio
async def test_cancellation_resistant_warning_never_delays_expiry_or_finalize(
    caplog: pytest.LogCaptureFixture,
) -> None:
    clock = AdvancingClock()
    release_warning = asyncio.Event()
    warning_cancelled = asyncio.Event()
    disconnect_times: list[float] = []
    fatal_shutdown_reasons: list[str] = []
    metadata = make_metadata(
        call_id="CALL_METADATA_SENTINEL",
        dispatch_token="DISPATCH_TOKEN_SENTINEL",
    )
    provider_text = "CANCELLATION_RESISTANT_PROVIDER_SENTINEL"

    async def stubborn_warning(_message: str) -> None:
        while not release_warning.is_set():
            try:
                await release_warning.wait()
            except asyncio.CancelledError:
                warning_cancelled.set()
        raise RuntimeError(provider_text)

    async def disconnect() -> None:
        disconnect_times.append(clock.now())

    runtime = SessionRuntime(
        FakeEventPublisher(),
        fatal_shutdown=fatal_shutdown_reasons.append,
        warning_callback=stubborn_warning,
        call_limit_clock=clock.now,
        call_limit_sleep=clock.sleep,
    )

    with caplog.at_level(logging.WARNING):
        runtime.enforce_call_limit(metadata, disconnect)
        timer = runtime.call_limit_task
        assert timer is not None
        try:
            for _ in range(20):
                if timer.done():
                    break
                await asyncio.sleep(0.01)

            assert warning_cancelled.is_set()
            assert disconnect_times == [120.0]
            assert timer.done()
            assert fatal_shutdown_reasons == [
                "call_limit_child_cleanup_timeout"
            ]
            await asyncio.wait_for(
                runtime.finalize(metadata, duration_seconds=120),
                timeout=0.1,
            )
            assert "call limit child cleanup timed out child=warning" in caplog.text
            assert "CALL_METADATA_SENTINEL" not in caplog.text
            assert "DISPATCH_TOKEN_SENTINEL" not in caplog.text
            assert provider_text not in caplog.text
            assert all(record.exc_info is None for record in caplog.records)
        finally:
            release_warning.set()
            await asyncio.wait_for(asyncio.shield(timer), timeout=0.1)

    for _ in range(10):
        if runtime.detached_call_limit_tasks == ():
            break
        await asyncio.sleep(0)

    assert runtime.detached_call_limit_tasks == ()
    assert metadata.call_id not in caplog.text
    assert metadata.dispatch_token not in caplog.text
    assert provider_text not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


@pytest.mark.anyio
async def test_hung_warning_is_cancelled_at_absolute_expiry() -> None:
    clock = AdvancingClock()
    warning_started = asyncio.Event()
    warning_cancelled = asyncio.Event()
    disconnect_times: list[float] = []
    fatal_shutdown_reasons: list[str] = []

    async def warn(_message: str) -> None:
        warning_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            warning_cancelled.set()

    async def disconnect() -> None:
        disconnect_times.append(clock.now())

    runtime = SessionRuntime(
        FakeEventPublisher(),
        fatal_shutdown=fatal_shutdown_reasons.append,
        warning_callback=warn,
        call_limit_clock=clock.now,
        call_limit_sleep=clock.sleep,
    )
    runtime.enforce_call_limit(make_metadata(), disconnect)

    await asyncio.wait_for(runtime.call_limit_task, timeout=0.2)

    assert warning_started.is_set()
    assert warning_cancelled.is_set()
    assert clock.sleep_calls == [60.0, 60.0]
    assert disconnect_times == [120.0]
    assert fatal_shutdown_reasons == []


@pytest.mark.anyio
async def test_disconnect_failure_is_safely_redacted(
    caplog: pytest.LogCaptureFixture,
) -> None:
    clock = AdvancingClock()
    fatal_shutdown_reasons: list[str] = []
    metadata = make_metadata(
        call_id="CALL_METADATA_SENTINEL",
        dispatch_token="DISPATCH_TOKEN_SENTINEL",
        allowed_duration_seconds=60,
    )

    async def disconnect() -> None:
        raise RuntimeError("DISCONNECT_PROVIDER_SENTINEL")

    runtime = SessionRuntime(
        FakeEventPublisher(),
        fatal_shutdown=fatal_shutdown_reasons.append,
        call_limit_clock=clock.now,
        call_limit_sleep=clock.sleep,
    )

    with caplog.at_level(logging.ERROR):
        runtime.enforce_call_limit(metadata, disconnect)
        await runtime.call_limit_task

    assert "call limit disconnect failed" in caplog.text
    assert "error_type=RuntimeError" in caplog.text
    assert "DISCONNECT_PROVIDER_SENTINEL" not in caplog.text
    assert metadata.call_id not in caplog.text
    assert metadata.dispatch_token not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)
    assert fatal_shutdown_reasons == ["call_limit_disconnect_failure"]
