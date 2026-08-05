import asyncio
import ipaddress
import math
import os
import time
from dataclasses import replace
from urllib.parse import urlsplit

import pytest
from arq.connections import ArqRedis, RedisSettings, create_pool
from arq.jobs import Job
from arq.worker import Worker, func

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


async def _enqueue(
    pool: ArqRedis,
    function_name: str,
    sequence: int,
    *,
    queue_name: str,
) -> Job:
    job = await asyncio.wait_for(
        pool.enqueue_job(function_name, sequence, _queue_name=queue_name),
        timeout=OPERATION_TIMEOUT_SECONDS,
    )
    assert job is not None
    return job


async def _cleanup(
    *,
    release_background: asyncio.Event,
    worker_tasks: list[asyncio.Task[None]],
    workers: list[Worker],
    pools: list[ArqRedis],
    control_pool: ArqRedis | None,
) -> None:
    cleanup_errors: list[BaseException] = []
    for worker in workers:
        worker.allow_pick_jobs = False
    release_background.set()

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

    background_started = 0
    all_background_started = asyncio.Event()
    release_background = asyncio.Event()
    lifecycle_started_at: dict[int, float] = {}

    async def background_probe(_ctx: dict[str, object], sequence: int) -> int:
        nonlocal background_started
        background_started += 1
        if background_started == 4:
            all_background_started.set()
        await release_background.wait()
        return sequence

    async def lifecycle_probe(_ctx: dict[str, object], sequence: int) -> int:
        lifecycle_started_at[sequence] = time.monotonic()
        return sequence

    pools: list[ArqRedis] = []
    workers: list[Worker] = []
    worker_tasks: list[asyncio.Task[None]] = []
    control_pool: ArqRedis | None = None
    primary_error: BaseException | None = None

    try:
        control_pool = await _create_test_pool(
            redis_settings,
            default_queue_name="arq:queue",
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
            queue_name="arq:queue:background",
            max_jobs=4,
            handle_signals=False,
            retry_jobs=False,
        )
        lifecycle_worker = Worker(
            functions=[func(lifecycle_probe, name="lifecycle_probe")],
            redis_pool=lifecycle_pool,
            queue_name="arq:queue",
            max_jobs=10,
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
            await _enqueue(
                control_pool,
                "background_probe",
                sequence,
                queue_name="arq:queue:background",
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
                await _enqueue(
                    control_pool,
                    "lifecycle_probe",
                    sequence,
                    queue_name="arq:queue",
                )
            )

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
