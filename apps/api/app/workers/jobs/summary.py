from app.providers.summaries.gemini import GeminiSummaryProvider
from app.services.summary_service import SummaryService


async def summary_job(ctx, payload: dict) -> dict:
    result = await SummaryService(provider=GeminiSummaryProvider()).create_summary(payload)
    return {
        "summary_text": result.text,
        "summary_data": result.data,
        "job_enqueued": result.job_enqueued,
    }
