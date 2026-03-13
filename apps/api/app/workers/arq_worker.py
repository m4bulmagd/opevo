from app.workers.jobs.notifications import notifications_job
from app.workers.jobs.recording import recording_job
from app.workers.jobs.summary import summary_job
from app.workers.jobs.transcript_flush import transcript_flush_job


class WorkerSettings:
    functions = [
        transcript_flush_job,
        summary_job,
        recording_job,
        notifications_job,
    ]
