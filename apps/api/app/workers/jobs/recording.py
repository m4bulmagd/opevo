from app.providers.storage.s3 import get_s3_storage
from app.services.recording_service import RecordingService


async def recording_job(ctx, payload: dict) -> dict:
    result = await RecordingService(provider=get_s3_storage()).store_recording(payload)
    return {
        "recording_key": result.object_key,
        "recording_url": result.url,
        "job_enqueued": result.job_enqueued,
    }
