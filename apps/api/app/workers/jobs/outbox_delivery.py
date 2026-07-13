import inspect
import logging
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.database import get_session_factory
from app.models.outbox_event import OutboxEvent
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
    }
)


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


async def outbox_delivery_job(ctx: dict[str, Any], _payload: dict | None = None) -> dict[str, int]:
    session_factory = ctx.get("session_factory") or get_session_factory()
    now_provider = ctx.get("outbox_now") or (lambda: datetime.now(UTC))
    handlers: Mapping[str, OutboxHandler] = (
        ctx["outbox_handlers"]
        if "outbox_handlers" in ctx
        else get_default_outbox_handlers()
    )
    result = {"claimed": 0, "delivered": 0, "retried": 0, "failed": 0}

    claim_time = now_provider()
    async with session_factory() as session:
        claimed = await OutboxRepository(session).claim_batch(
            limit=OUTBOX_BATCH_SIZE,
            now=claim_time,
        )
        await session.commit()
    result["claimed"] = len(claimed)

    for event in claimed:
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
                await session.commit()
            if stored is None:
                continue
            if stored.status == "failed":
                result["failed"] += 1
                metric = ctx.get("outbox_terminal_failure_metric")
                metric = metric or emit_outbox_terminal_failure_metric
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


async def outbox_reconciliation_job(ctx: dict[str, Any]) -> dict[str, int]:
    return await outbox_delivery_job(ctx)


def get_default_outbox_handlers() -> Mapping[str, OutboxHandler]:
    # Imports stay local so worker startup does not eagerly initialize provider SDKs.
    from app.workers.jobs.outbox_topics import DEFAULT_OUTBOX_HANDLERS

    return DEFAULT_OUTBOX_HANDLERS
