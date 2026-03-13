from app.services.summary_service import SummaryService


async def summary_job(payload: dict) -> dict:
    result = SummaryService().create_summary(payload)
    return {
        "summary_text": result.text,
        "job_enqueued": result.job_enqueued,
    }
