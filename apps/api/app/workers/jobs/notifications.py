from uuid import UUID

from app.core.database import get_session_factory
from app.services.notification_service import NotificationService


async def notifications_job(payload: dict) -> dict:
    session_factory = get_session_factory()
    async with session_factory() as session:
        result = await NotificationService(session).create_call_completed_notification(
            user_id=payload["user_id"],
            call_id=UUID(str(payload["call_id"])),
            summary_text=payload.get("summary_text"),
            minutes_charged=payload["minutes_charged"],
        )
        await session.commit()
    return {
        "status": result.status,
        "job_enqueued": result.job_enqueued,
    }
