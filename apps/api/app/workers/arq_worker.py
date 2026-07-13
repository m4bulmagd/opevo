from arq.connections import RedisSettings
from arq.cron import cron

from app.core.config import get_settings
from app.core.logging import setup_logging
from app.workers.jobs.call_finalization import call_finalization_job
from app.workers.jobs.notifications import notifications_job
from app.workers.jobs.outbox_delivery import outbox_delivery_job, outbox_reconciliation_job
from app.workers.jobs.recording import recording_job
from app.workers.jobs.summary import summary_job
from app.workers.jobs.transcript_flush import transcript_flush_job


async def on_startup(_ctx: dict) -> None:
    setup_logging()


class WorkerSettings:
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    on_startup = on_startup
    functions = [
        call_finalization_job,
        transcript_flush_job,
        summary_job,
        recording_job,
        notifications_job,
        outbox_delivery_job,
    ]
    cron_jobs = [
        cron(
            outbox_reconciliation_job,
            minute=set(range(60)),
            name="outbox_reconciliation_job",
        )
    ]
