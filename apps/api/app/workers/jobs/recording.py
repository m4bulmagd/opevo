from app.services.recording_service import RecordingService


async def recording_job(ctx, payload: dict) -> dict:
    result = await RecordingService().store_recording(payload)
    return {
        "recording_key": result.object_key,
        "recording_url": result.url,
        "job_enqueued": result.job_enqueued,
    }
