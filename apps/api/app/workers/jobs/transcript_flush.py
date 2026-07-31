from uuid import UUID

from presvo_contracts import TranscriptSegment

from app.core.database import get_session_factory
from app.services.transcript_service import TranscriptService


async def transcript_flush_job(ctx, payload: dict) -> dict:
    call_id = UUID(str(payload["call_id"]))
    transcript = tuple(
        TranscriptSegment.model_validate(item)
        for item in (payload.get("transcript") or ())
    )
    session_factory = get_session_factory()
    async with session_factory() as session:
        service = TranscriptService(session)
        await service.merge_recovery(
            call_id=call_id,
            transcript=transcript,
        )
        await session.commit()
    return payload
