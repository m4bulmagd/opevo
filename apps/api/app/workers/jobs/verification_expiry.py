import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any

from app.composition.runtime import require_background_runtime
from app.core.database import AsyncSessionFactory
from app.services.forwarding_verification_service import (
    DEFAULT_EXPIRY_BATCH_SIZE,
    ForwardingVerificationService,
)


logger = logging.getLogger(__name__)


async def expire_verification_windows(
    *,
    session_factory: AsyncSessionFactory,
    now: Callable[[], datetime],
    batch_size: int = DEFAULT_EXPIRY_BATCH_SIZE,
) -> dict[str, int]:
    async with session_factory() as session:
        expired = await ForwardingVerificationService(
            session,
            now_provider=now,
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


async def verification_expiry_job(
    ctx: dict[str, Any],
) -> dict[str, int]:
    runtime = require_background_runtime(ctx)
    return await expire_verification_windows(
        session_factory=runtime.session_factory,
        now=runtime.now,
    )
