from typing import Any
from uuid import UUID

from app.composition.runtime import require_call_lifecycle_runtime
from app.core.database import AsyncSessionFactory
from app.services.call_lifecycle_service import CallLifecycleService


async def finalize_call(
    payload: dict,
    *,
    session_factory: AsyncSessionFactory,
) -> dict:
    if not isinstance(payload, dict) or set(payload) != {"call_id"}:
        raise ValueError("Call finalization payload must contain call_id only")
    call_id = UUID(payload["call_id"])
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


async def call_finalization_job(
    ctx: dict[str, Any],
    payload: dict,
) -> dict:
    runtime = require_call_lifecycle_runtime(ctx)
    return await finalize_call(
        payload,
        session_factory=runtime.session_factory,
    )
