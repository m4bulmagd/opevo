from uuid import UUID

from app.core.database import get_session_factory
from app.services.transcript_service import TranscriptService


async def transcript_flush_job(ctx, payload: dict) -> dict:
    session_factory = get_session_factory()
    async with session_factory() as session:
        service = TranscriptService(session)
        await service.merge_recovery(
            call_id=UUID(str(payload["call_id"])),
            transcript=payload.get("transcript") or [],
        )
        await session.commit()
    return payload
