import logging
from datetime import UTC, datetime

from app.core.database import get_session_factory
from app.core.config import get_settings
from app.core.logging import report_safe_exception
from app.repositories.call_repository import CallRepository
from app.services.call_reconciliation_service import CallReconciliationService


logger = logging.getLogger(__name__)


async def call_reconciliation_job(ctx: dict) -> dict[str, int]:
    session_factory = ctx.get("session_factory") or get_session_factory()
    now_provider = ctx.get("call_reconciliation_now") or (lambda: datetime.now(UTC))
    now = now_provider()
    result = await CallReconciliationService(session_factory).reconcile(
        now,
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
    response = {
        "scanned": result.scanned,
        "recovered": result.recovered,
        "failed": result.failed,
        "deferred": result.deferred,
    }
    telemetry = ctx.get("observability")
    if telemetry is not None:
        telemetry.record_reconciliation_outcomes(response)
        if hasattr(telemetry, "record_call_snapshot"):
            try:
                async with session_factory() as session:
                    snapshot = await CallRepository(session).observability_snapshot(
                        now,
                        get_settings(),
                    )
                telemetry.record_call_snapshot(snapshot)
            except Exception as error:
                report_safe_exception(
                    logger,
                    event="observability_snapshot_failed",
                    operation="collect_call_snapshot",
                    error=error,
                    status="failed",
                    level=logging.WARNING,
                )
    return response
