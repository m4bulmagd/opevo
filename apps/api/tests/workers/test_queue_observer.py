import asyncio
import logging

import pytest


class _Redis:
    def __init__(self, *, depths: list[object], ranges: list[object]) -> None:
        self._depths = iter(depths)
        self._ranges = iter(ranges)
        self.calls: list[tuple[object, ...]] = []
        self.close_calls = 0
        self.aclose_calls = 0

    async def zcard(self, queue_name: str) -> object:
        self.calls.append(("zcard", queue_name))
        result = next(self._depths)
        if isinstance(result, BaseException):
            raise result
        return result

    async def zrange(
        self,
        queue_name: str,
        start: int,
        end: int,
        *,
        withscores: bool,
    ) -> object:
        self.calls.append(("zrange", queue_name, start, end, withscores))
        result = next(self._ranges)
        if isinstance(result, BaseException):
            raise result
        return result

    async def close(self) -> None:
        self.close_calls += 1

    async def aclose(self) -> None:
        self.aclose_calls += 1


class _Telemetry:
    def __init__(self) -> None:
        self.snapshots: list[tuple[str, int, float]] = []
        self.recorded = asyncio.Event()

    def record_worker_queue_snapshot(
        self,
        queue_class: str,
        *,
        depth: int,
        oldest_due_age_seconds: float,
    ) -> None:
        self.snapshots.append((queue_class, depth, oldest_due_age_seconds))
        self.recorded.set()


def _observer(redis: _Redis, telemetry: _Telemetry, **kwargs):
    from app.workers.queue_observer import QueueObserver

    return QueueObserver(
        redis,
        telemetry,
        queue_name="arq:queue",
        queue_class="call_lifecycle",
        now=lambda: 12.5,
        **kwargs,
    )


@pytest.mark.anyio
async def test_sample_records_depth_and_oldest_due_age_from_scores_only() -> None:
    """Changing the score conversion or queue read contract breaks this snapshot."""
    redis = _Redis(depths=[3], ranges=[[(b"PRIVATE_PAYLOAD", 10_000.0)]])
    telemetry = _Telemetry()
    observer = _observer(redis, telemetry)

    await observer.sample()

    assert redis.calls == [
        ("zcard", "arq:queue"),
        ("zrange", "arq:queue", 0, 0, True),
    ]
    assert telemetry.snapshots == [
        ("call_lifecycle", 3, pytest.approx(2.5)),
    ]
    assert redis.close_calls == 0
    assert redis.aclose_calls == 0


@pytest.mark.anyio
async def test_sample_records_zero_age_for_empty_queue() -> None:
    """A missing oldest job must not leave a stale queue-age gauge behind."""
    redis = _Redis(depths=[0], ranges=[])
    telemetry = _Telemetry()

    await _observer(redis, telemetry).sample()

    assert redis.calls == [("zcard", "arq:queue")]
    assert telemetry.snapshots == [("call_lifecycle", 0, 0.0)]


@pytest.mark.anyio
async def test_sample_clamps_future_oldest_score_to_zero_age() -> None:
    """Clock skew must not export a negative queue age."""
    redis = _Redis(depths=[1], ranges=[[(b"ignored", 13_000.0)]])
    telemetry = _Telemetry()

    await _observer(redis, telemetry).sample()

    assert telemetry.snapshots == [("call_lifecycle", 1, 0.0)]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("depth", "items", "expected_depth"),
    [
        ("not-an-int", [], 0),
        (2, [], 2),
        (2, [(b"ignored", "not-a-score")], 2),
        (2, object(), 2),
    ],
)
async def test_sample_handles_racy_or_invalid_replies_without_payload_access(
    depth: object,
    items: object,
    expected_depth: int,
) -> None:
    """Malformed Redis replies must degrade to zero age without reading a member."""
    redis = _Redis(depths=[depth], ranges=[items])
    telemetry = _Telemetry()

    await _observer(redis, telemetry).sample()

    assert telemetry.snapshots == [("call_lifecycle", expected_depth, 0.0)]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("items", "now"),
    [
        ([(b"ignored",)], 12.5),
        ([(b"ignored", 10_000.0)], float("nan")),
    ],
)
async def test_sample_rejects_malformed_score_pairs_and_nonfinite_clock(
    items: object,
    now: float,
) -> None:
    """Malformed Redis scores or a broken clock must export a safe zero age."""
    from app.workers.queue_observer import QueueObserver

    redis = _Redis(depths=[1], ranges=[items])
    telemetry = _Telemetry()
    observer = QueueObserver(
        redis,
        telemetry,
        queue_name="arq:queue",
        queue_class="call_lifecycle",
        now=lambda: now,
    )

    await observer.sample()

    assert telemetry.snapshots == [("call_lifecycle", 1, 0.0)]


@pytest.mark.anyio
async def test_runner_logs_safe_failure_then_samples_again(monkeypatch, caplog) -> None:
    """A transient Redis failure must not stop later observation cycles."""
    redis = _Redis(
        depths=[RuntimeError("PRIVATE_REDIS_DETAIL"), 1],
        ranges=[[(b"ignored", 10_000.0)]],
    )
    telemetry = _Telemetry()
    first_sleep_complete = asyncio.Event()
    block_after_retry = asyncio.Event()

    async def controlled_sleep(_seconds: float) -> None:
        if not first_sleep_complete.is_set():
            first_sleep_complete.set()
            return
        await block_after_retry.wait()

    monkeypatch.setattr("app.workers.queue_observer.asyncio.sleep", controlled_sleep)
    observer = _observer(redis, telemetry)

    with caplog.at_level(logging.WARNING):
        observer.start()
        await telemetry.recorded.wait()
        await observer.aclose()

    assert redis.calls == [
        ("zcard", "arq:queue"),
        ("zcard", "arq:queue"),
        ("zrange", "arq:queue", 0, 0, True),
    ]
    assert telemetry.snapshots == [("call_lifecycle", 1, pytest.approx(2.5))]
    assert caplog.messages == [
        "worker queue observation failed queue_class=call_lifecycle error_type=unknown"
    ]
    assert "PRIVATE_REDIS_DETAIL" not in caplog.text


@pytest.mark.anyio
async def test_start_twice_runs_one_background_observer(monkeypatch) -> None:
    """Repeated startup hooks must not duplicate queue telemetry."""
    redis = _Redis(depths=[1], ranges=[[(b"ignored", 10_000.0)]])
    telemetry = _Telemetry()
    sleep_started = asyncio.Event()
    release_sleep = asyncio.Event()

    async def blocking_sleep(_seconds: float) -> None:
        sleep_started.set()
        await release_sleep.wait()

    monkeypatch.setattr("app.workers.queue_observer.asyncio.sleep", blocking_sleep)
    observer = _observer(redis, telemetry)

    observer.start()
    observer.start()
    await sleep_started.wait()
    await observer.aclose()

    assert telemetry.snapshots == [("call_lifecycle", 1, pytest.approx(2.5))]
    assert redis.close_calls == 0
    assert redis.aclose_calls == 0


@pytest.mark.anyio
async def test_aclose_twice_cancels_sleeper_once_without_closing_redis(monkeypatch) -> None:
    """The observer owns only its task, not the ARQ Redis connection."""
    redis = _Redis(depths=[1], ranges=[[(b"ignored", 10_000.0)]])
    telemetry = _Telemetry()
    sleep_started = asyncio.Event()

    async def blocking_sleep(_seconds: float) -> None:
        sleep_started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr("app.workers.queue_observer.asyncio.sleep", blocking_sleep)
    observer = _observer(redis, telemetry)
    observer.start()
    await sleep_started.wait()

    await observer.aclose()
    await observer.aclose()

    assert redis.close_calls == 0
    assert redis.aclose_calls == 0


@pytest.mark.anyio
async def test_aclose_propagates_external_cancellation(monkeypatch) -> None:
    """Cancelling shutdown must not be mistaken for owned-task cancellation."""
    redis = _Redis(depths=[1], ranges=[[(b"ignored", 10_000.0)]])
    telemetry = _Telemetry()
    sleep_started = asyncio.Event()
    owned_cancellation_started = asyncio.Event()

    async def cancellation_cleanup(_seconds: float) -> None:
        sleep_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            owned_cancellation_started.set()
            await asyncio.Event().wait()

    monkeypatch.setattr("app.workers.queue_observer.asyncio.sleep", cancellation_cleanup)
    observer = _observer(redis, telemetry)
    observer.start()
    await sleep_started.wait()

    close_task = asyncio.create_task(observer.aclose())
    await owned_cancellation_started.wait()
    close_task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await close_task
    await observer.aclose()


@pytest.mark.anyio
async def test_aclose_propagates_unexpected_observer_task_failure(monkeypatch) -> None:
    """Shutdown must surface terminal failures from the owned observer task."""
    redis = _Redis(depths=[1], ranges=[[(b"ignored", 10_000.0)]])
    telemetry = _Telemetry()
    terminal_error = RuntimeError("observer sleep failed")

    async def failing_sleep(_seconds: float) -> None:
        raise terminal_error

    monkeypatch.setattr("app.workers.queue_observer.asyncio.sleep", failing_sleep)
    observer = _observer(redis, telemetry)
    observer.start()
    await telemetry.recorded.wait()

    with pytest.raises(RuntimeError) as captured:
        await observer.aclose()
    assert captured.value is terminal_error
    await observer.aclose()
    assert redis.close_calls == 0
    assert redis.aclose_calls == 0


@pytest.mark.anyio
async def test_runner_propagates_cancellation_from_sleep(monkeypatch) -> None:
    """Cancellation is control flow and must not become a Redis warning."""
    redis = _Redis(depths=[1], ranges=[[(b"ignored", 10_000.0)]])
    telemetry = _Telemetry()
    sleep_started = asyncio.Event()

    async def blocking_sleep(_seconds: float) -> None:
        sleep_started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr("app.workers.queue_observer.asyncio.sleep", blocking_sleep)
    observer = _observer(redis, telemetry)
    task = asyncio.create_task(observer._run())
    await sleep_started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.anyio
async def test_runner_propagates_cancellation_from_redis_without_warning(caplog) -> None:
    """Redis cancellation is worker control flow, not an observation failure."""
    redis = _Redis(depths=[asyncio.CancelledError()], ranges=[])
    telemetry = _Telemetry()

    with caplog.at_level(logging.WARNING):
        with pytest.raises(asyncio.CancelledError):
            await _observer(redis, telemetry)._run()

    assert telemetry.snapshots == []
    assert caplog.messages == []


@pytest.mark.anyio
async def test_member_payload_never_enters_logs_or_metrics(caplog) -> None:
    """Queue members are opaque payloads and must remain unobserved."""
    payload_sentinel = b"PRIVATE_QUEUE_PAYLOAD"
    redis = _Redis(depths=[1], ranges=[[(payload_sentinel, 10_000.0)]])
    telemetry = _Telemetry()

    with caplog.at_level(logging.WARNING):
        await _observer(redis, telemetry).sample()

    rendered = repr(telemetry.snapshots) + caplog.text
    assert payload_sentinel.decode() not in rendered
