from uuid import UUID

from app.core.database import get_session_factory
from app.services.call_lifecycle_service import CallLifecycleService


async def call_finalization_job(ctx, payload: dict) -> dict:
    if not isinstance(payload, dict) or set(payload) != {"call_id"}:
        raise ValueError("Call finalization payload must contain call_id only")
    call_id = UUID(payload["call_id"])
    session_factory = ctx.get("session_factory") or get_session_factory()
    async with session_factory() as session:
        service = CallLifecycleService(session)
        claim = await service.claim_finalization(call_id)
        if claim.unavailable:
            return {"status": "failed"}
        if claim.already_completed:
            return {"status": "skipped"}
        result = await service.complete_finalization(
            call_id,
            generation=claim.generation,
        )
        if result.stale_generation:
            return {
                "status": "stale",
                "minutes_charged": result.minutes_charged,
            }
    return {
        "status": "skipped" if result.already_completed else "completed",
        "minutes_charged": result.minutes_charged,
    }
