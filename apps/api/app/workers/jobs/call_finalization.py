from uuid import UUID
import logging

from arq.worker import Retry
from redis.exceptions import LockError

from app.core.database import get_session_factory
from app.services.call_lifecycle_service import CallLifecycleService


logger = logging.getLogger(__name__)

async def call_finalization_job(ctx, payload: dict) -> dict:
    call_id = payload.get("call_id")
    redis = ctx["redis"]
    lock_key = f"ai_call:finalization:lock:{call_id}"
    
    try:
        async with redis.lock(lock_key, timeout=60, blocking_timeout=5):
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
    except LockError as exc:
        logger.warning(f"Could not acquire lock {lock_key}. Retrying...")
        raise Retry(defer=10) from exc
