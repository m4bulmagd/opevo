import inspect
import logging
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from app.core.database import get_session_factory
from app.core.observability import bind_call_id, get_observability
from app.models.outbox_event import OutboxEvent
from app.repositories.call_repository import CallRepository
from app.repositories.outbox_repository import OutboxRepository
from app.services.outbox_service import OutboxPayloadError, validate_outbox_payload


logger = logging.getLogger(__name__)

OUTBOX_RETRY_DELAYS = (
    timedelta(seconds=10),
    timedelta(minutes=1),
    timedelta(minutes=5),
    timedelta(minutes=30),
    timedelta(hours=2),
)
OUTBOX_BATCH_SIZE = 100

OutboxHandler = Callable[[dict[str, Any], OutboxEvent], Awaitable[None]]


SAFE_OUTBOX_ERROR_CODES = frozenset(
    {
        "provider_retryable",
        "provider_terminal",
        "unsupported_topic",
        "invalid_payload",
        "handler_configuration",
        "dispatch_ineligible",
        "dispatch_conflict",
        "dispatch_configuration",
        "summary_stale",
    }
)

_CALL_TOPIC_AGGREGATE_TYPES = {
    "livekit.dispatch": "call",
    "summary.generate": "call-summary",
    "recording.stop": "call-recording",
}


class OutboxDeliveryError(RuntimeError):
    def __init__(self, error_code: str, *, retryable: bool) -> None:
        if error_code not in SAFE_OUTBOX_ERROR_CODES:
            raise ValueError("Unsafe outbox error code")
        super().__init__(error_code)
        self.error_code = error_code
        self.retryable = retryable


def _classify_error(error: BaseException) -> tuple[str, bool]:
    if isinstance(error, OutboxDeliveryError):
        return error.error_code, error.retryable
    if isinstance(error, OutboxPayloadError):
        return "invalid_payload", False
    return "provider_retryable", True


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


def _outbox_error_class(error_code: str) -> str:
    return {
        "provider_retryable": "unavailable",
        "provider_terminal": "unknown",
        "unsupported_topic": "validation",
        "invalid_payload": "validation",
        "handler_configuration": "validation",
        "dispatch_ineligible": "validation",
        "dispatch_conflict": "conflict",
        "dispatch_configuration": "validation",
        "summary_stale": "conflict",
    }.get(error_code, "unknown")


async def outbox_delivery_job(
    ctx: dict[str, Any],
    _payload: dict | None = None,
) -> dict[str, int]:
    session_factory = ctx.get("session_factory") or get_session_factory()
    now_provider = ctx.get("outbox_now") or (lambda: datetime.now(UTC))
    handlers: Mapping[str, OutboxHandler] = (
        ctx["outbox_handlers"]
        if "outbox_handlers" in ctx
        else get_default_outbox_handlers()
    )
    result = {"claimed": 0, "delivered": 0, "retried": 0, "failed": 0}

    for _ in range(OUTBOX_BATCH_SIZE):
        claim_time = now_provider()
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
                await handler(ctx, event)
        except Exception as error:
            error_code, retryable = _classify_error(error)
            failure_time = now_provider()
            async with session_factory() as session:
                stored = await OutboxRepository(session).mark_failed_attempt(
                    event_id=event.id,
                    attempt_count=attempt_count,
                    failed_at=failure_time,
                    error_code=error_code,
                    retry_delays=OUTBOX_RETRY_DELAYS,
                    terminal=not retryable,
                )
                if stored is not None and stored.status == "failed":
                    await _fail_livekit_dispatch_call(
                        session,
                        event=stored,
                        error_code=error_code,
                    )
                await session.commit()
            if stored is None:
                continue
            if stored.status == "failed":
                result["failed"] += 1
                metric = ctx.get("outbox_terminal_failure_metric")
                if metric is None:
                    telemetry = ctx.get("observability") or get_observability()

                    def metric(topic: str, code: str) -> None:
                        telemetry.record_outbox_terminal_failure(
                            topic,
                            _outbox_error_class(code),
                        )
                metric_result = metric(event.topic, error_code)
                if inspect.isawaitable(metric_result):
                    await metric_result
            else:
                result["retried"] += 1
            continue

        delivered_at = now_provider()
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
    }.get(error_code, "dispatch_provider_exhausted")
    await CallRepository(session).mark_dispatch_failed(
        call,
        failure_code=failure_code,
    )


async def outbox_reconciliation_job(ctx: dict[str, Any]) -> dict[str, int]:
    result = await outbox_delivery_job(ctx)
    telemetry = ctx.get("observability") or get_observability()
    session_factory = ctx.get("session_factory") or get_session_factory()
    try:
        async with session_factory() as session:
            snapshot = await OutboxRepository(session).observability_snapshot(
                datetime.now(UTC)
            )
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
    return result


def get_default_outbox_handlers() -> Mapping[str, OutboxHandler]:
    # Imports stay local so worker startup does not eagerly initialize provider SDKs.
    from app.workers.jobs.outbox_topics import DEFAULT_OUTBOX_HANDLERS

    return DEFAULT_OUTBOX_HANDLERS
