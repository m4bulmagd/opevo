import logging
from datetime import UTC, datetime

from app.core.database import get_session_factory
from app.services.call_reconciliation_service import CallReconciliationService


logger = logging.getLogger(__name__)


async def call_reconciliation_job(ctx: dict) -> dict[str, int]:
    session_factory = ctx.get("session_factory") or get_session_factory()
    now_provider = ctx.get("call_reconciliation_now") or (lambda: datetime.now(UTC))
    result = await CallReconciliationService(session_factory).reconcile(
        now_provider(),
        limit=100,
    )
    logger.info(
        "call reconciliation completed scanned=%d recovered=%d failed=%d deferred=%d",
        result.scanned,
        result.recovered,
        result.failed,
        result.deferred,
        extra={
            "event": "call_reconciliation_completed",
            "operation": "reconcile_calls",
            "status": "completed",
            "scanned": result.scanned,
            "recovered": result.recovered,
            "failed": result.failed,
            "deferred": result.deferred,
        },
    )
    return {
        "scanned": result.scanned,
        "recovered": result.recovered,
        "failed": result.failed,
        "deferred": result.deferred,
    }
