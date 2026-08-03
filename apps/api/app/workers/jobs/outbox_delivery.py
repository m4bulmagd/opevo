import inspect
import logging
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from app.core.database import get_session_factory
from app.core.logging import report_safe_exception
from app.core.observability import bind_call_id, get_observability
from app.core.provider_failures import ProviderFailure
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
from app.services.outbox_service import OutboxPayloadError, validate_outbox_payload
from app.services.recording_lifecycle_service import RecordingLifecycleService


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
        "internal_defect",
        "unsupported_topic",
        "invalid_payload",
        "handler_configuration",
        "dispatch_ineligible",
        "dispatch_conflict",
        "dispatch_configuration",
        "summary_stale",
        "recording_unresolved",
        "recording_provider_unavailable",
        "recording_storage_unavailable",
        "recording_identity_mismatch",
        "recording_identity_conflict",
        "recording_legacy_incomplete",
        "account_call_draining",
        "subscription_authentication",
        "subscription_contract",
        "telephony_authentication",
        "telephony_release_conflict",
        "provider_contract",
    }
)

_CALL_TOPIC_AGGREGATE_TYPES = {
    "livekit.dispatch": "call",
    "summary.generate": "call-summary",
}


class OutboxDeliveryError(RuntimeError):
    def __init__(
        self,
        error_code: str,
        *,
        retryable: bool,
        exhaustible: bool = True,
    ) -> None:
        if error_code not in SAFE_OUTBOX_ERROR_CODES:
            raise ValueError("Unsafe outbox error code")
        if not retryable and not exhaustible:
            raise ValueError("Non-retryable outbox errors must be exhaustible")
        super().__init__(error_code)
        self.error_code = error_code
        self.retryable = retryable
        self.exhaustible = exhaustible


def provider_failure_delivery_error(error: ProviderFailure) -> OutboxDeliveryError:
    return OutboxDeliveryError(
        "provider_retryable" if error.retryable else "provider_terminal",
        retryable=error.retryable,
    )


def _classify_error(error: Exception) -> tuple[str, bool, bool]:
    if isinstance(error, OutboxDeliveryError):
        return error.error_code, error.retryable, error.exhaustible
    if isinstance(error, OutboxPayloadError):
        return "invalid_payload", False, True
    if isinstance(error, ProviderFailure):
        return (
            "provider_retryable" if error.retryable else "provider_terminal",
            error.retryable,
            True,
        )
    return "internal_defect", False, True


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


def _outbox_error_class(error_code: str) -> str:
    return {
        "provider_retryable": "unavailable",
        "provider_terminal": "unknown",
        "internal_defect": "unknown",
        "unsupported_topic": "validation",
        "invalid_payload": "validation",
        "handler_configuration": "validation",
        "dispatch_ineligible": "validation",
        "dispatch_conflict": "conflict",
        "dispatch_configuration": "validation",
        "summary_stale": "conflict",
        "recording_unresolved": "unknown",
        "recording_provider_unavailable": "unavailable",
        "recording_storage_unavailable": "unavailable",
        "recording_identity_mismatch": "validation",
        "recording_identity_conflict": "conflict",
        "recording_legacy_incomplete": "validation",
        "account_call_draining": "unavailable",
        "subscription_authentication": "authentication",
        "subscription_contract": "validation",
        "telephony_authentication": "authentication",
        "telephony_release_conflict": "conflict",
        "provider_contract": "validation",
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
            error_code, retryable, exhaustible = _classify_error(error)
            if error_code == "internal_defect":
                _report_internal_defect(error)
            failure_time = now_provider()
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
        "internal_defect": "dispatch_internal_defect",
    }.get(error_code, "dispatch_provider_exhausted")
    await CallRepository(session).mark_dispatch_failed(
        call,
        failure_code=failure_code,
    )
    await RecordingLifecycleService(session).request_stop(call)


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

    recording_now_provider = ctx.get(
        "recording_observability_now",
        lambda: datetime.now(UTC),
    )
    try:
        async with session_factory() as session:
            recording_snapshot = await RecordingEgressOperationRepository(
                session
            ).observability_snapshot(recording_now_provider())
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

    deactivation_now_provider = ctx.get(
        "account_deactivation_observability_now",
        lambda: datetime.now(UTC),
    )
    try:
        async with session_factory() as session:
            deactivation_snapshot = await AccountDeactivationRepository(
                session
            ).observability_snapshot(deactivation_now_provider())
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


def get_default_outbox_handlers() -> Mapping[str, OutboxHandler]:
    # Imports stay local so worker startup does not eagerly initialize provider SDKs.
    from app.workers.jobs.outbox_topics import DEFAULT_OUTBOX_HANDLERS

    return DEFAULT_OUTBOX_HANDLERS
