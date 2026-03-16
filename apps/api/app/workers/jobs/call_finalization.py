from uuid import UUID

from app.core.database import get_session_factory
from app.services.call_lifecycle_service import CallLifecycleService


async def call_finalization_job(payload: dict) -> dict:
    session_factory = get_session_factory()
    async with session_factory() as session:
        result = await CallLifecycleService(session).finalize_call(
            {
                **payload,
                "user_id": UUID(str(payload["user_id"])),
            }
        )
    return {
        "status": "skipped" if result.already_completed else "completed",
        "minutes_charged": result.minutes_charged,
        "summary_text": result.summary_text,
        "recording_key": result.recording_key,
        "number_disabled": result.number_disabled,
    }
