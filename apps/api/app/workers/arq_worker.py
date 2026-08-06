from typing import Any, cast

from arq.connections import RedisSettings
from arq.cron import cron
from arq.typing import WorkerCoroutine
from arq.worker import func

from app.composition.runtime import (
    WORKER_RUNTIME_KEY,
    BackgroundWorkerRuntime,
    CallLifecycleWorkerRuntime,
    WorkerRuntimeConfigurationError,
    require_worker_observability,
)
from app.composition.workers import (
    build_background_worker_runtime,
    build_call_lifecycle_worker_runtime,
)
from app.core.config import get_settings
from app.core.logging import install_arq_worker_log_sanitizer, setup_logging
from app.workers.jobs.call_finalization import call_finalization_job
from app.workers.jobs.call_reconciliation import call_reconciliation_job
from app.workers.outbox.delivery import (
    outbox_delivery_job,
    outbox_reconciliation_job,
)
from app.workers.jobs.verification_expiry import verification_expiry_job
from app.workers.job_policy import (
    CALL_FINALIZATION_POLICY,
    CALL_RECONCILIATION_POLICY,
    OUTBOX_DELIVERY_POLICY,
    OUTBOX_RECONCILIATION_POLICY,
    VERIFICATION_EXPIRY_POLICY,
    apply_job_policy,
)
from app.workers.queueing import (
    BACKGROUND_QUEUE_NAME,
    CALL_LIFECYCLE_QUEUE_NAME,
    QUEUE_CLASS_BACKGROUND,
    QUEUE_CLASS_CALL_LIFECYCLE,
)


_WORKER_SETTINGS = get_settings()


async def on_call_lifecycle_startup(ctx: dict[str, Any]) -> None:
    setup_logging()
    install_arq_worker_log_sanitizer()
    runtime = await build_call_lifecycle_worker_runtime(
        _WORKER_SETTINGS,
        arq_redis=ctx["redis"],
    )
    ctx[WORKER_RUNTIME_KEY] = runtime


async def on_background_startup(ctx: dict[str, Any]) -> None:
    setup_logging()
    install_arq_worker_log_sanitizer()
    runtime = await build_background_worker_runtime(
        _WORKER_SETTINGS,
        arq_redis=ctx["redis"],
    )
    ctx[WORKER_RUNTIME_KEY] = runtime


async def on_shutdown(ctx: dict[str, Any]) -> None:
    runtime = ctx.pop(WORKER_RUNTIME_KEY, None)
    if runtime is None:
        return
    if isinstance(runtime, CallLifecycleWorkerRuntime):
        await runtime.aclose()
        return
    if isinstance(runtime, BackgroundWorkerRuntime):
        await runtime.aclose()
        return
    raise WorkerRuntimeConfigurationError("worker runtime has an invalid type")


policy_call_finalization_job = cast(
    WorkerCoroutine,
    apply_job_policy(
        call_finalization_job,
        policy=CALL_FINALIZATION_POLICY,
        queue_class=QUEUE_CLASS_CALL_LIFECYCLE,
        observability_getter=require_worker_observability,
    ),
)
policy_outbox_delivery_job = cast(
    WorkerCoroutine,
    apply_job_policy(
        outbox_delivery_job,
        policy=OUTBOX_DELIVERY_POLICY,
        queue_class=QUEUE_CLASS_BACKGROUND,
        observability_getter=require_worker_observability,
    ),
)
policy_outbox_reconciliation_job = cast(
    WorkerCoroutine,
    apply_job_policy(
        outbox_reconciliation_job,
        policy=OUTBOX_RECONCILIATION_POLICY,
        queue_class=QUEUE_CLASS_BACKGROUND,
        observability_getter=require_worker_observability,
    ),
)
policy_call_reconciliation_job = cast(
    WorkerCoroutine,
    apply_job_policy(
        call_reconciliation_job,
        policy=CALL_RECONCILIATION_POLICY,
        queue_class=QUEUE_CLASS_CALL_LIFECYCLE,
        observability_getter=require_worker_observability,
    ),
)
policy_verification_expiry_job = cast(
    WorkerCoroutine,
    apply_job_policy(
        verification_expiry_job,
        policy=VERIFICATION_EXPIRY_POLICY,
        queue_class=QUEUE_CLASS_BACKGROUND,
        observability_getter=require_worker_observability,
    ),
)


class CallLifecycleWorkerSettings:
    redis_settings = RedisSettings.from_dsn(_WORKER_SETTINGS.redis_url)
    on_startup = on_call_lifecycle_startup
    on_shutdown = on_shutdown
    queue_name = CALL_LIFECYCLE_QUEUE_NAME
    max_jobs = _WORKER_SETTINGS.worker_lifecycle_max_jobs
    poll_delay = 0.5
    job_completion_wait = 60
    health_check_interval = 15
    health_check_key = "presvo:worker:call-lifecycle:health"
    functions = [
        func(
            policy_call_finalization_job,
            name=CALL_FINALIZATION_POLICY.arq_name,
            timeout=CALL_FINALIZATION_POLICY.hard_timeout_seconds,
            max_tries=CALL_FINALIZATION_POLICY.max_tries,
        ),
        func(
            policy_call_reconciliation_job,
            name=CALL_RECONCILIATION_POLICY.arq_name,
            keep_result=0,
            timeout=CALL_RECONCILIATION_POLICY.hard_timeout_seconds,
            max_tries=CALL_RECONCILIATION_POLICY.max_tries,
        ),
    ]
    cron_jobs = [
        cron(
            policy_call_reconciliation_job,
            minute=set(range(60)),
            name=CALL_RECONCILIATION_POLICY.arq_name,
            keep_result=0,
            timeout=CALL_RECONCILIATION_POLICY.hard_timeout_seconds,
            max_tries=CALL_RECONCILIATION_POLICY.max_tries,
        ),
    ]


class BackgroundWorkerSettings:
    redis_settings = RedisSettings.from_dsn(_WORKER_SETTINGS.redis_url)
    on_startup = on_background_startup
    on_shutdown = on_shutdown
    queue_name = BACKGROUND_QUEUE_NAME
    max_jobs = _WORKER_SETTINGS.worker_background_max_jobs
    poll_delay = 0.5
    job_completion_wait = 30
    health_check_interval = 15
    health_check_key = "presvo:worker:background:health"
    functions = [
        func(
            policy_outbox_delivery_job,
            name=OUTBOX_DELIVERY_POLICY.arq_name,
            timeout=OUTBOX_DELIVERY_POLICY.hard_timeout_seconds,
            max_tries=OUTBOX_DELIVERY_POLICY.max_tries,
        ),
    ]
    cron_jobs = [
        cron(
            policy_outbox_reconciliation_job,
            minute=set(range(60)),
            name=OUTBOX_RECONCILIATION_POLICY.arq_name,
            keep_result=0,
            timeout=OUTBOX_RECONCILIATION_POLICY.hard_timeout_seconds,
            max_tries=OUTBOX_RECONCILIATION_POLICY.max_tries,
        ),
        cron(
            policy_verification_expiry_job,
            minute=set(range(60)),
            name=VERIFICATION_EXPIRY_POLICY.arq_name,
            keep_result=0,
            timeout=VERIFICATION_EXPIRY_POLICY.hard_timeout_seconds,
            max_tries=VERIFICATION_EXPIRY_POLICY.max_tries,
        ),
    ]
