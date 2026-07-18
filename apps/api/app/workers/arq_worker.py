from arq.connections import RedisSettings
from arq.cron import cron

from app.core.config import get_settings
from app.core.logging import setup_logging
from app.core.observability import (
    initialize_observability,
    instrument_job,
    shutdown_observability,
)
from app.core.runtime_validation import validate_worker_runtime
from app.workers.jobs.call_finalization import call_finalization_job
from app.workers.jobs.call_reconciliation import call_reconciliation_job
from app.workers.jobs.outbox_delivery import outbox_delivery_job, outbox_reconciliation_job
from app.workers.jobs.transcript_flush import transcript_flush_job
from app.workers.jobs.verification_expiry import verification_expiry_job


async def on_startup(ctx: dict) -> None:
    setup_logging()
    settings = get_settings()
    validate_worker_runtime(settings)
    ctx["observability"] = initialize_observability(
        service_name="presvo-worker",
        endpoint=settings.otel_exporter_otlp_endpoint,
    )


async def on_shutdown(ctx: dict) -> None:
    telemetry = ctx.get("observability")
    if telemetry is not None:
        await shutdown_observability(telemetry)


observed_call_finalization_job = instrument_job("call_finalization")(
    call_finalization_job
)
observed_transcript_flush_job = instrument_job("transcript_flush")(
    transcript_flush_job
)
observed_outbox_delivery_job = instrument_job("outbox_delivery")(
    outbox_delivery_job
)
observed_outbox_reconciliation_job = instrument_job("outbox_reconciliation")(
    outbox_reconciliation_job
)
observed_call_reconciliation_job = instrument_job("call_reconciliation")(
    call_reconciliation_job
)
observed_verification_expiry_job = instrument_job("verification_expiry")(
    verification_expiry_job
)


class WorkerSettings:
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    on_startup = on_startup
    on_shutdown = on_shutdown
    functions = [
        observed_call_finalization_job,
        observed_transcript_flush_job,
        observed_outbox_delivery_job,
        observed_call_reconciliation_job,
    ]
    cron_jobs = [
        cron(
            observed_outbox_reconciliation_job,
            minute=set(range(60)),
            name="outbox_reconciliation_job",
        ),
        cron(
            observed_call_reconciliation_job,
            minute=set(range(60)),
            name="call_reconciliation_job",
        ),
        cron(
            observed_verification_expiry_job,
            minute=set(range(60)),
            name="verification_expiry_job",
        ),
    ]
