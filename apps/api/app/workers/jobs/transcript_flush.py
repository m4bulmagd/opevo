from uuid import UUID

from app.core.database import get_session_factory
from app.repositories.message_repository import MessageRepository


async def transcript_flush_job(ctx, payload: dict) -> dict:
    session_factory = get_session_factory()
    async with session_factory() as session:
        repository = MessageRepository(session)
        await repository.create_many(
            call_id=UUID(str(payload["call_id"])),
            transcript=payload.get("transcript") or [],
        )
        await session.commit()
    return payload
