from arq.connections import RedisSettings

from app.core.config import get_settings
from app.workers.jobs.call_finalization import call_finalization_job
from app.workers.jobs.notifications import notifications_job
from app.workers.jobs.recording import recording_job
from app.workers.jobs.summary import summary_job
from app.workers.jobs.phone_provisioning import phone_provisioning_job
from app.workers.jobs.transcript_flush import transcript_flush_job


class WorkerSettings:
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    functions = [
        call_finalization_job,
        transcript_flush_job,
        summary_job,
        recording_job,
        notifications_job,
        phone_provisioning_job,
    ]
