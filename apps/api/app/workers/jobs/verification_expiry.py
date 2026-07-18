import logging
from datetime import UTC, datetime

from app.core.database import get_session_factory
from app.services.forwarding_verification_service import (
    DEFAULT_EXPIRY_BATCH_SIZE,
    ForwardingVerificationService,
)


logger = logging.getLogger(__name__)


async def verification_expiry_job(ctx: dict) -> dict[str, int]:
    session_factory = ctx.get("session_factory") or get_session_factory()
    now_provider = ctx.get("verification_expiry_now") or (
        lambda: datetime.now(UTC)
    )
    batch_size = ctx.get(
        "verification_expiry_batch_size",
        DEFAULT_EXPIRY_BATCH_SIZE,
    )
    async with session_factory() as session:
        expired = await ForwardingVerificationService(
            session,
            now_provider=now_provider,
        ).expire_batch(limit=batch_size)
    logger.info(
        "verification expiry completed expired=%d",
        expired,
        extra={
            "event": "verification_expiry_completed",
            "operation": "expire_verification_windows",
            "status": "completed",
            "expired": expired,
        },
    )
    return {"expired": expired}
