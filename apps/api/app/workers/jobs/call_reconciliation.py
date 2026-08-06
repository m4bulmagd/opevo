import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any

from arq.connections import ArqRedis

from app.composition.runtime import require_call_lifecycle_runtime
from app.core.config import Settings
from app.core.database import AsyncSessionFactory
from app.core.logging import report_safe_exception
from app.core.observability import Observability
from app.repositories.call_repository import CallRepository
from app.services.call_reconciliation_service import CallReconciliationService
from app.workers.queueing import enqueue_outbox_wakeup


logger = logging.getLogger(__name__)


async def reconcile_calls(
    *,
    session_factory: AsyncSessionFactory,
    arq_pool: ArqRedis,
    observability: Observability,
    settings: Settings,
    now: Callable[[], datetime],
) -> dict[str, int]:
    current_time = now()
    result = await CallReconciliationService(
        session_factory,
        settings=settings,
    ).reconcile(
        current_time,
        limit=100,
    )
    if result.scanned and arq_pool is not None:
        try:
            await enqueue_outbox_wakeup(arq_pool)
        except Exception:
            logger.warning(
                "outbox wakeup enqueue failed operation=call_reconciliation "
                "error_type=unknown"
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
    observability.record_reconciliation_outcomes(response)
    if hasattr(observability, "record_call_snapshot"):
        try:
            async with session_factory() as session:
                snapshot = await CallRepository(session).observability_snapshot(
                    current_time,
                    settings,
                )
            observability.record_call_snapshot(snapshot)
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


async def call_reconciliation_job(
    ctx: dict[str, Any],
) -> dict[str, int]:
    runtime = require_call_lifecycle_runtime(ctx)
    return await reconcile_calls(
        session_factory=runtime.session_factory,
        arq_pool=runtime.arq_pool,
        observability=runtime.observability,
        settings=runtime.settings,
        now=runtime.now,
    )
