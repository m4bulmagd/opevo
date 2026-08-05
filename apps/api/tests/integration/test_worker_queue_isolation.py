import asyncio
import ipaddress
import logging
import math
import os
import time
from collections.abc import Awaitable
from dataclasses import replace
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
from arq.connections import ArqRedis, RedisSettings, create_pool
from arq.jobs import Job
from arq.worker import Worker, func
from sqlalchemy.exc import OperationalError

from app.core.config import Settings
from app.workers import arq_worker
from app.workers.arq_worker import (
    BackgroundWorkerSettings,
    CallLifecycleWorkerSettings,
)


TEST_DATABASE = 15
CONNECTION_TIMEOUT_SECONDS = 5.0
OPERATION_TIMEOUT_SECONDS = 5.0


def _dedicated_redis_settings(redis_url: str) -> RedisSettings:
    parsed = urlsplit(redis_url)
    host = parsed.hostname
    if parsed.scheme not in {"redis", "rediss"} or host is None:
        pytest.fail("TEST_REDIS_URL must be a Redis URL with a loopback host")

    try:
        is_loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        is_loopback = host.casefold() == "localhost"
    if not is_loopback:
        pytest.fail("TEST_REDIS_URL must target a loopback Redis")

    return replace(RedisSettings.from_dsn(redis_url), database=TEST_DATABASE)


async def _create_test_pool(
    redis_settings: RedisSettings,
    *,
    default_queue_name: str,
) -> ArqRedis:
    return await asyncio.wait_for(
        create_pool(
            redis_settings,
            default_queue_name=default_queue_name,
        ),
        timeout=CONNECTION_TIMEOUT_SECONDS,
    )


async def _require_enqueued(enqueue: Awaitable[Job | None]) -> Job:
    job = await asyncio.wait_for(
        enqueue,
        timeout=OPERATION_TIMEOUT_SECONDS,
    )
    assert job is not None
    return job


async def _cleanup(
    *,
    release_background: asyncio.Event,
    release_lifecycle: asyncio.Event,
    worker_tasks: list[asyncio.Task[None]],
    workers: list[Worker],
    pools: list[ArqRedis],
    control_pool: ArqRedis | None,
) -> None:
    cleanup_errors: list[BaseException] = []
    for worker in workers:
        worker.allow_pick_jobs = False
    release_background.set()
    release_lifecycle.set()

    running_jobs = [
        task
        for worker in workers
        for task in worker.tasks.values()
        if not task.done()
    ]
    if running_jobs:
        try:
            job_results = await asyncio.wait_for(
                asyncio.gather(*running_jobs, return_exceptions=True),
                timeout=OPERATION_TIMEOUT_SECONDS,
            )
        except BaseException as exc:
            cleanup_errors.append(exc)
        else:
            cleanup_errors.extend(
                result
                for result in job_results
                if isinstance(result, BaseException)
                and not isinstance(result, asyncio.CancelledError)
            )

    for task in worker_tasks:
        task.cancel()
    if worker_tasks:
        try:
            task_results = await asyncio.wait_for(
                asyncio.gather(*worker_tasks, return_exceptions=True),
                timeout=OPERATION_TIMEOUT_SECONDS,
            )
        except BaseException as exc:
            cleanup_errors.append(exc)
        else:
            cleanup_errors.extend(
                result
                for result in task_results
                if isinstance(result, BaseException)
                and not isinstance(result, asyncio.CancelledError)
            )

    for worker in workers:
        try:
            await asyncio.wait_for(
                worker.close(),
                timeout=OPERATION_TIMEOUT_SECONDS,
            )
        except BaseException as exc:
            cleanup_errors.append(exc)

    if control_pool is not None:
        try:
            await asyncio.wait_for(
                control_pool.flushdb(),
                timeout=OPERATION_TIMEOUT_SECONDS,
            )
        except BaseException as exc:
            cleanup_errors.append(exc)

    for pool in reversed(pools):
        try:
            await asyncio.wait_for(
                pool.aclose(close_connection_pool=True),
                timeout=OPERATION_TIMEOUT_SECONDS,
            )
        except BaseException as exc:
            cleanup_errors.append(exc)

    if cleanup_errors:
        details = ", ".join(
            f"{type(error).__name__}: {error}" for error in cleanup_errors
        )
        raise RuntimeError(
            f"real-Redis worker test cleanup failed: {details}"
        ) from cleanup_errors[0]


@pytest.mark.asyncio
@pytest.mark.filterwarnings(
    "ignore:Call to deprecated close.*:DeprecationWarning:arq.worker"
)
async def test_lifecycle_queue_starts_ten_jobs_while_background_is_saturated() -> None:
    redis_url = os.getenv("TEST_REDIS_URL")
    if redis_url is None:
        pytest.skip("dedicated Redis is required; set TEST_REDIS_URL")

    redis_settings = _dedicated_redis_settings(redis_url)
    assert redis_settings.database == 15
    assert CallLifecycleWorkerSettings.queue_name == "arq:queue"
    assert CallLifecycleWorkerSettings.max_jobs == 10
    assert BackgroundWorkerSettings.queue_name == "arq:queue:background"
    assert BackgroundWorkerSettings.max_jobs == 4
    assert Settings.model_fields["worker_lifecycle_max_jobs"].default == 10
    assert Settings.model_fields["worker_background_max_jobs"].default == 4

    background_started = 0
    all_background_started = asyncio.Event()
    release_background = asyncio.Event()
    lifecycle_started = 0
    all_lifecycle_started = asyncio.Event()
    release_lifecycle = asyncio.Event()
    lifecycle_started_at: dict[int, float] = {}

    async def background_probe(_ctx: dict[str, object], sequence: int) -> int:
        nonlocal background_started
        background_started += 1
        if background_started == 4:
            all_background_started.set()
        await release_background.wait()
        return sequence

    async def lifecycle_probe(_ctx: dict[str, object], sequence: int) -> int:
        nonlocal lifecycle_started
        lifecycle_started_at[sequence] = time.monotonic()
        lifecycle_started += 1
        if lifecycle_started == 10:
            all_lifecycle_started.set()
        await release_lifecycle.wait()
        return sequence

    pools: list[ArqRedis] = []
    workers: list[Worker] = []
    worker_tasks: list[asyncio.Task[None]] = []
    control_pool: ArqRedis | None = None
    primary_error: BaseException | None = None

    try:
        control_pool = await _create_test_pool(
            redis_settings,
            default_queue_name="arq:queue:unrouted-test",
        )
        pools.append(control_pool)
        await asyncio.wait_for(
            control_pool.flushdb(),
            timeout=OPERATION_TIMEOUT_SECONDS,
        )

        background_pool = await _create_test_pool(
            redis_settings,
            default_queue_name="arq:queue:background",
        )
        pools.append(background_pool)
        lifecycle_pool = await _create_test_pool(
            redis_settings,
            default_queue_name="arq:queue",
        )
        pools.append(lifecycle_pool)

        background_worker = Worker(
            functions=[func(background_probe, name="background_probe")],
            redis_pool=background_pool,
            queue_name=BackgroundWorkerSettings.queue_name,
            max_jobs=BackgroundWorkerSettings.max_jobs,
            handle_signals=False,
            retry_jobs=False,
        )
        lifecycle_worker = Worker(
            functions=[func(lifecycle_probe, name="lifecycle_probe")],
            redis_pool=lifecycle_pool,
            queue_name=CallLifecycleWorkerSettings.queue_name,
            max_jobs=CallLifecycleWorkerSettings.max_jobs,
            handle_signals=False,
            retry_jobs=False,
        )
        workers.extend([background_worker, lifecycle_worker])
        worker_tasks.extend(
            [
                asyncio.create_task(
                    background_worker.async_run(),
                    name="background-arq-worker",
                ),
                asyncio.create_task(
                    lifecycle_worker.async_run(),
                    name="lifecycle-arq-worker",
                ),
            ]
        )

        background_jobs = [
            await _require_enqueued(
                control_pool.enqueue_job(
                    "background_probe",
                    sequence,
                    _queue_name="arq:queue:background",
                )
            )
            for sequence in range(4)
        ]
        await asyncio.wait_for(
            all_background_started.wait(),
            timeout=OPERATION_TIMEOUT_SECONDS,
        )
        assert background_started == 4

        enqueued_at: dict[int, float] = {}
        lifecycle_jobs: list[Job] = []
        for sequence in range(10):
            enqueued_at[sequence] = time.monotonic()
            lifecycle_jobs.append(
                await _require_enqueued(
                    control_pool.enqueue_job(
                        "lifecycle_probe",
                        sequence,
                        _queue_name="arq:queue",
                    )
                )
            )

        await asyncio.wait_for(
            all_lifecycle_started.wait(),
            timeout=OPERATION_TIMEOUT_SECONDS,
        )
        assert lifecycle_started == 10
        assert background_started == 4
        assert not release_background.is_set()
        assert not release_lifecycle.is_set()

        release_lifecycle.set()
        lifecycle_results = await asyncio.wait_for(
            asyncio.gather(
                *(
                    job.result(timeout=4.0, poll_delay=0.01)
                    for job in lifecycle_jobs
                )
            ),
            timeout=OPERATION_TIMEOUT_SECONDS,
        )
        assert lifecycle_results == list(range(10))
        assert not release_background.is_set()

        delays = sorted(
            lifecycle_started_at[index] - enqueued_at[index] for index in range(10)
        )
        p95 = delays[math.ceil(0.95 * len(delays)) - 1]
        assert p95 <= 2.0

        release_background.set()
        background_results = await asyncio.wait_for(
            asyncio.gather(
                *(job.result(timeout=4.0, poll_delay=0.01) for job in background_jobs)
            ),
            timeout=OPERATION_TIMEOUT_SECONDS,
        )
        assert background_results == list(range(4))
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        cleanup_task = asyncio.create_task(
            _cleanup(
                release_background=release_background,
                release_lifecycle=release_lifecycle,
                worker_tasks=worker_tasks,
                workers=workers,
                pools=pools,
                control_pool=control_pool,
            ),
            name="worker-isolation-cleanup",
        )
        try:
            await asyncio.shield(cleanup_task)
        except asyncio.CancelledError:
            await cleanup_task
            raise
        except BaseException as cleanup_error:
            if primary_error is None:
                raise
            primary_error.add_note(str(cleanup_error))


@pytest.mark.asyncio
@pytest.mark.filterwarnings(
    "ignore:Call to deprecated close.*:DeprecationWarning:arq.worker"
)
async def test_real_worker_job_logs_are_fixed_and_payload_blind(
    monkeypatch,
    caplog,
) -> None:
    redis_url = os.getenv("TEST_REDIS_URL")
    if redis_url is None:
        pytest.skip("dedicated Redis is required; set TEST_REDIS_URL")

    redis_settings = _dedicated_redis_settings(redis_url)
    queue_name = "arq:queue:logging-privacy"
    call_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    finalization_job_id = f"call-finalization:{call_id}"
    payload_sentinel = "PRIVATE_FINALIZATION_PAYLOAD"
    result_sentinel = "PRIVATE_FINALIZATION_RESULT"
    unknown_function = "PRIVATE_UNKNOWN_FUNCTION"
    unknown_job_id = "private-unknown-job-id"
    sql_job_id = "private-sql-job-id"
    sql_sentinel = "SELECT PRIVATE_SQL_DETAIL FROM calls"
    driver_sentinel = "PRIVATE_DRIVER_DETAIL"

    async def finalization_probe(
        _ctx: dict[str, object], payload: dict[str, str]
    ) -> str:
        assert payload == {
            "call_id": call_id,
            "private": payload_sentinel,
        }
        return result_sentinel

    async def sql_failure_probe(_ctx: dict[str, object]) -> None:
        raise OperationalError(
            sql_sentinel,
            {"private": payload_sentinel},
            RuntimeError(driver_sentinel),
        )

    class NoopObserver:
        def start(self) -> None:
            pass

        async def aclose(self) -> None:
            pass

    monkeypatch.setattr(
        arq_worker,
        "get_settings",
        lambda: SimpleNamespace(otel_exporter_otlp_endpoint=None),
    )
    monkeypatch.setattr(arq_worker, "validate_worker_runtime", lambda _settings: None)
    monkeypatch.setattr(
        arq_worker,
        "initialize_observability",
        lambda **_kwargs: object(),
    )
    monkeypatch.setattr(arq_worker, "QueueObserver", lambda *_args, **_kwargs: NoopObserver())

    async def shutdown_observability(_telemetry: object) -> None:
        pass

    monkeypatch.setattr(
        arq_worker,
        "shutdown_observability",
        shutdown_observability,
    )

    control_pool: ArqRedis | None = None
    worker_pool: ArqRedis | None = None
    worker: Worker | None = None
    try:
        control_pool = await _create_test_pool(
            redis_settings,
            default_queue_name="arq:queue:unrouted-test",
        )
        await asyncio.wait_for(control_pool.flushdb(), OPERATION_TIMEOUT_SECONDS)
        worker_pool = await _create_test_pool(
            redis_settings,
            default_queue_name=queue_name,
        )
        worker = Worker(
            functions=[
                func(finalization_probe, name="call_finalization_job"),
                func(sql_failure_probe, name="sql_failure_probe"),
            ],
            redis_pool=worker_pool,
            queue_name=queue_name,
            on_startup=arq_worker.on_call_lifecycle_startup,
            on_shutdown=arq_worker.on_shutdown,
            burst=True,
            handle_signals=False,
            retry_jobs=False,
        )
        await _require_enqueued(
            control_pool.enqueue_job(
                "call_finalization_job",
                {"call_id": call_id, "private": payload_sentinel},
                _job_id=finalization_job_id,
                _queue_name=queue_name,
            )
        )
        await _require_enqueued(
            control_pool.enqueue_job(
                unknown_function,
                {"private": payload_sentinel},
                _job_id=unknown_job_id,
                _queue_name=queue_name,
            )
        )
        await _require_enqueued(
            control_pool.enqueue_job(
                "sql_failure_probe",
                _job_id=sql_job_id,
                _queue_name=queue_name,
            )
        )

        caplog.set_level(logging.DEBUG, logger="arq.worker")
        await asyncio.wait_for(worker.async_run(), OPERATION_TIMEOUT_SECONDS)
        assert worker.jobs_complete == 1
        assert worker.jobs_failed == 2
    finally:
        if worker is not None:
            await asyncio.wait_for(worker.close(), OPERATION_TIMEOUT_SECONDS)
            worker_pool = None
        if control_pool is not None:
            await asyncio.wait_for(control_pool.flushdb(), OPERATION_TIMEOUT_SECONDS)
        for pool in (worker_pool, control_pool):
            if pool is not None:
                await asyncio.wait_for(
                    pool.aclose(close_connection_pool=True),
                    OPERATION_TIMEOUT_SECONDS,
                )

    arq_records = [record for record in caplog.records if record.name == "arq.worker"]
    messages = [record.getMessage() for record in arq_records]
    expected_events = {
        "arq worker job started",
        "arq worker job completed",
        "arq worker function not registered",
        "arq worker job failed",
    }
    assert expected_events <= set(messages)
    assert any(
        record.levelno == logging.WARNING
        and record.getMessage() == "arq worker function not registered"
        for record in arq_records
    )
    assert any(
        record.levelno == logging.ERROR
        and record.getMessage() == "arq worker job failed"
        for record in arq_records
    )

    lifecycle_records = [
        record for record in arq_records if record.getMessage() in expected_events
    ]
    assert lifecycle_records
    assert all(record.args == () for record in lifecycle_records)
    assert all(record.exc_info is None for record in lifecycle_records)
    assert all(record.exc_text is None for record in lifecycle_records)
    assert all(record.stack_info is None for record in lifecycle_records)

    rendered_records = "\n".join(
        f"{record.getMessage()} {record.__dict__!r}" for record in arq_records
    )
    for private_value in (
        call_id,
        finalization_job_id,
        payload_sentinel,
        result_sentinel,
        unknown_function,
        unknown_job_id,
        sql_job_id,
        sql_sentinel,
        driver_sentinel,
        "OperationalError",
    ):
        assert private_value not in rendered_records
