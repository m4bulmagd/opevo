from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID

from app.composition.runtime import require_background_runtime
from app.core.database import AsyncSessionFactory
from app.core.logging import report_safe_exception
from app.core.observability import Observability, bind_call_id
from app.models.outbox_event import OutboxEvent
from app.repositories.account_deactivation_repository import (
    AccountDeactivationRepository,
)
from app.repositories.call_repository import CallRepository
from app.repositories.outbox_repository import OutboxRepository
from app.repositories.recording_egress_operation_repository import (
    RecordingEgressOperationRepository,
)
from app.services.activation_go_live_service import fail_current_go_live_attempt
from app.services.outbox_service import validate_outbox_payload
from app.services.recording_lifecycle_service import RecordingLifecycleService
from app.workers.outbox.failures import (
    OutboxDeliveryError,
    _classify_error,
    _outbox_error_class,
)

if TYPE_CHECKING:
    from app.workers.outbox.registry import OutboxHandler


logger = logging.getLogger(__name__)

OUTBOX_RETRY_DELAYS = (
    timedelta(seconds=10),
    timedelta(minutes=1),
    timedelta(minutes=5),
    timedelta(minutes=30),
    timedelta(hours=2),
)
OUTBOX_BATCH_SIZE = 100

_CALL_TOPIC_AGGREGATE_TYPES = {
    "livekit.dispatch": "call",
    "summary.generate": "call-summary",
}


def _report_internal_defect(error: Exception) -> None:
    try:
        report_safe_exception(
            logger,
            event="outbox_internal_defect",
            operation="deliver_outbox_event",
            error=error,
            provider="internal",
            status="failed",
            level=logging.CRITICAL,
        )
    except Exception:
        return


async def emit_outbox_terminal_failure_metric(
    topic: str,
    error_code: str,
) -> None:
    logger.error(
        "outbox terminal failure topic=%s error_code=%s",
        topic,
        error_code,
        extra={
            "event": "outbox_terminal_failure",
            "operation": "deliver_outbox_event",
            "status": "failed",
            "count": 1,
        },
    )


async def deliver_outbox_batch(
    *,
    session_factory: AsyncSessionFactory,
    handlers: Mapping[str, OutboxHandler],
    observability: Observability,
    now: Callable[[], datetime],
) -> dict[str, int]:
    result = {"claimed": 0, "delivered": 0, "retried": 0, "failed": 0}

    for _ in range(OUTBOX_BATCH_SIZE):
        claim_time = now()
        async with session_factory() as session:
            claimed = await OutboxRepository(session).claim_batch(
                limit=1,
                now=claim_time,
            )
            await session.commit()
        if not claimed:
            break
        event = claimed[0]
        result["claimed"] += 1
        attempt_count = event.attempt_count
        try:
            try:
                handler = handlers[event.topic]
            except KeyError:
                raise OutboxDeliveryError(
                    "unsupported_topic",
                    retryable=False,
                ) from None
            validate_outbox_payload(event.topic, event.payload)
            with bind_call_id(_validated_event_call_id(event)):
                await handler(event)
        except Exception as error:
            error_code, retryable, exhaustible = _classify_error(error)
            if error_code == "internal_defect":
                _report_internal_defect(error)
            failure_time = now()
            async with session_factory() as session:
                stored = await OutboxRepository(session).mark_failed_attempt(
                    event_id=event.id,
                    attempt_count=attempt_count,
                    failed_at=failure_time,
                    error_code=error_code,
                    retry_delays=OUTBOX_RETRY_DELAYS,
                    terminal=not retryable,
                    exhaustible=exhaustible,
                )
                if stored is not None and stored.status == "failed":
                    await _fail_livekit_dispatch_call(
                        session,
                        event=stored,
                        error_code=error_code,
                    )
                    await fail_current_go_live_attempt(
                        session,
                        event=stored,
                    )
                await session.commit()
            if stored is None:
                continue
            if stored.status == "failed":
                result["failed"] += 1
                observability.record_outbox_terminal_failure(
                    event.topic,
                    _outbox_error_class(error_code),
                )
            else:
                result["retried"] += 1
            continue

        delivered_at = now()
        async with session_factory() as session:
            stored = await OutboxRepository(session).mark_delivered(
                event_id=event.id,
                attempt_count=attempt_count,
                delivered_at=delivered_at,
            )
            await session.commit()
        if stored is not None:
            result["delivered"] += 1

    return result


def _validated_event_call_id(event: OutboxEvent) -> str | None:
    expected_aggregate_type = _CALL_TOPIC_AGGREGATE_TYPES.get(event.topic)
    if (
        expected_aggregate_type is None
        or event.aggregate_type != expected_aggregate_type
    ):
        return None
    try:
        call_id = UUID(str(event.payload["call_id"]))
    except (KeyError, TypeError, ValueError, AttributeError):
        return None
    if event.aggregate_id != call_id:
        return None
    return str(call_id)


async def _fail_livekit_dispatch_call(
    session,
    *,
    event: OutboxEvent,
    error_code: str,
) -> None:
    if event.topic != "livekit.dispatch" or event.aggregate_type != "call":
        return
    call = await CallRepository(session).get_by_id_for_update(event.aggregate_id)
    if call is None or call.status != "pending":
        return
    failure_code = {
        "dispatch_ineligible": "dispatch_ineligible",
        "dispatch_conflict": "dispatch_conflict",
        "dispatch_configuration": "dispatch_configuration",
        "invalid_payload": "dispatch_configuration",
        "handler_configuration": "dispatch_configuration",
        "provider_retryable": "dispatch_provider_exhausted",
        "provider_terminal": "dispatch_provider_exhausted",
        "internal_defect": "dispatch_internal_defect",
    }.get(error_code, "dispatch_provider_exhausted")
    await CallRepository(session).mark_dispatch_failed(
        call,
        failure_code=failure_code,
    )
    await RecordingLifecycleService(session).request_stop(call)


async def reconcile_outbox(
    *,
    session_factory: AsyncSessionFactory,
    handlers: Mapping[str, OutboxHandler],
    observability: Observability,
    now: Callable[[], datetime],
) -> dict[str, int]:
    result = await deliver_outbox_batch(
        session_factory=session_factory,
        handlers=handlers,
        observability=observability,
        now=now,
    )
    telemetry = observability
    try:
        async with session_factory() as session:
            snapshot = await OutboxRepository(session).observability_snapshot(now())
        telemetry.record_outbox_snapshot(snapshot)
    except Exception as error:
        from app.core.logging import report_safe_exception

        report_safe_exception(
            logger,
            event="observability_snapshot_failed",
            operation="collect_outbox_snapshot",
            error=error,
            status="failed",
            level=logging.WARNING,
        )

    try:
        async with session_factory() as session:
            recording_snapshot = await RecordingEgressOperationRepository(
                session
            ).observability_snapshot(now())
        telemetry.record_recording_operation_snapshot(recording_snapshot)
    except Exception as error:
        from app.core.logging import report_safe_exception

        report_safe_exception(
            logger,
            event="observability_snapshot_failed",
            operation="collect_recording_operation_snapshot",
            error=error,
            status="failed",
            level=logging.WARNING,
        )

    try:
        async with session_factory() as session:
            deactivation_snapshot = await AccountDeactivationRepository(
                session
            ).observability_snapshot(now())
        telemetry.record_account_deactivation_snapshot(deactivation_snapshot)
    except Exception as error:
        from app.core.logging import report_safe_exception

        report_safe_exception(
            logger,
            event="observability_snapshot_failed",
            operation="collect_account_deactivation_snapshot",
            error=error,
            status="failed",
            level=logging.WARNING,
        )
    return result


async def outbox_delivery_job(
    ctx: dict[str, Any], _payload: dict | None = None
) -> dict[str, int]:
    runtime = require_background_runtime(ctx)
    return await deliver_outbox_batch(
        session_factory=runtime.session_factory,
        handlers=runtime.outbox_handlers,
        observability=runtime.observability,
        now=runtime.now,
    )


async def outbox_reconciliation_job(ctx: dict[str, Any]) -> dict[str, int]:
    runtime = require_background_runtime(ctx)
    return await reconcile_outbox(
        session_factory=runtime.session_factory,
        handlers=runtime.outbox_handlers,
        observability=runtime.observability,
        now=runtime.now,
    )
