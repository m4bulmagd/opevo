from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import TYPE_CHECKING, Protocol
from uuid import UUID

from app.core.database import AsyncSessionFactory
from app.core.observability import Observability
from app.core.provider_failures import ProviderFailure
from app.models.outbox_event import OutboxEvent
from app.providers.summaries.base import SummaryProvider
from app.providers.livekit_recording.base import RecordingProvider
from app.providers.storage.base import StorageProvider
from app.repositories.call_repository import CallRepository
from app.repositories.message_repository import MessageRepository
from app.services.summary_service import SummaryService
from app.workers.outbox.failures import (
    OutboxDeliveryError,
    provider_failure_delivery_error,
)


if TYPE_CHECKING:
    from app.workers.outbox.recording_reconciliation import ReconciliationResult


class _RecordingReconciler(Protocol):
    async def reconcile(self, operation_id: UUID) -> ReconciliationResult: ...


async def deliver_summary_generate(
    event: OutboxEvent,
    *,
    session_factory: AsyncSessionFactory,
    summary_provider: SummaryProvider,
) -> None:
    call_id = _validated_post_call_reference(
        event,
        topic="summary.generate",
        aggregate_type="call-summary",
    )
    async with session_factory() as session:
        call = await CallRepository(session).get_by_id(call_id)
        if call is None:
            await session.rollback()
            raise OutboxDeliveryError("provider_terminal", retryable=False)
        messages = await MessageRepository(session).list_by_call_id(call_id)
        transcript_max_sequence = messages[-1].sequence_number if messages else 0
        if (
            call.summary_transcript_max_sequence is not None
            and call.summary_transcript_max_sequence >= transcript_max_sequence
            and (transcript_max_sequence == 0 or call.summary_data is not None)
        ):
            await session.commit()
            return
        transcript = [
            {"speaker": message.speaker, "text": message.text} for message in messages
        ]
        await session.commit()

    summary_data = None
    if transcript:
        try:
            structured = await summary_provider.generate_summary(transcript)
            summary_data = SummaryService.validate_structured_summary(structured)
        except ProviderFailure as exc:
            raise provider_failure_delivery_error(exc) from None
        if summary_data is None:
            raise OutboxDeliveryError("provider_terminal", retryable=False)

    async with session_factory() as session:
        call = await CallRepository(session).get_by_id_for_update(call_id)
        if call is None:
            await session.rollback()
            raise OutboxDeliveryError("provider_terminal", retryable=False)
        durable_max_sequence = await MessageRepository(session).max_sequence_by_call_id(
            call_id
        )
        if durable_max_sequence != transcript_max_sequence:
            await session.rollback()
            raise OutboxDeliveryError("summary_stale", retryable=True)
        if (
            call.summary_transcript_max_sequence is not None
            and call.summary_transcript_max_sequence >= durable_max_sequence
            and (durable_max_sequence == 0 or call.summary_data is not None)
        ):
            await session.commit()
            return
        if summary_data is not None:
            call.summary_text = summary_data["summary_text"]
            call.summary_data = summary_data
        call.summary_transcript_max_sequence = durable_max_sequence
        await session.flush()
        await session.commit()


def build_recording_reconciler(
    *,
    session_factory: AsyncSessionFactory,
    recording_provider: RecordingProvider,
    storage_provider: StorageProvider,
    now: Callable[[], datetime],
) -> _RecordingReconciler:
    from app.workers.outbox.recording_reconciliation import RecordingReconciler

    return RecordingReconciler(
        session_factory,
        recording_provider,
        storage_provider,
        now_provider=now,
    )


async def deliver_recording_reconcile(
    event: OutboxEvent,
    *,
    session_factory: AsyncSessionFactory,
    recording_provider: RecordingProvider,
    storage_provider: StorageProvider,
    observability: Observability,
    now: Callable[[], datetime],
) -> None:
    operation_id = _validated_recording_operation_reference(event)
    try:
        reconciler = build_recording_reconciler(
            session_factory=session_factory,
            recording_provider=recording_provider,
            storage_provider=storage_provider,
            now=now,
        )
    except ProviderFailure as error:
        raise provider_failure_delivery_error(error) from error
    await reconcile_recording_operation(
        operation_id,
        reconciler=reconciler,
        observability=observability,
    )


async def reconcile_recording_operation(
    operation_id: UUID,
    *,
    reconciler: _RecordingReconciler,
    observability: Observability,
) -> None:
    try:
        from app.workers.outbox.recording_reconciliation import (
            RECORDING_RECONCILIATION_ERROR_CODES,
        )

        result = await reconciler.reconcile(operation_id)
        conflict_category = result.conflict_category
        if conflict_category not in {None, "multiple_exact_match"}:
            raise ValueError("Recording reconciliation conflict is invalid")
        if conflict_category == "multiple_exact_match" and (
            result.outcome != "retry"
            or result.error_code != "recording_identity_conflict"
        ):
            raise ValueError("Recording reconciliation conflict shape is invalid")
        if result.outcome == "complete":
            if result.error_code is not None:
                raise ValueError("Completed reconciliation returned an error")
            result_label = "complete"
        elif result.outcome == "retry":
            error_code = result.error_code or "recording_unresolved"
            if error_code not in RECORDING_RECONCILIATION_ERROR_CODES:
                raise ValueError("Recording reconciliation error is invalid")
            result_label = error_code
        else:
            raise ValueError("Recording reconciliation outcome is invalid")
    except ProviderFailure as error:
        raise provider_failure_delivery_error(error) from error

    observability.record_recording_reconciliation_result(result_label)
    if conflict_category == "multiple_exact_match":
        observability.record_multiple_exact_match_conflict()
    if result.outcome == "complete":
        return
    raise OutboxDeliveryError(
        error_code,
        retryable=True,
        exhaustible=False,
    )


def _validated_recording_operation_reference(event: OutboxEvent) -> UUID:
    try:
        operation_id = UUID(event.payload["operation_id"])
    except (KeyError, TypeError, ValueError):
        raise OutboxDeliveryError("invalid_payload", retryable=False) from None
    if (
        event.topic != "recording.reconcile"
        or event.aggregate_type != "recording-egress-operation"
        or event.aggregate_id != operation_id
        or event.payload != {"operation_id": str(operation_id)}
    ):
        raise OutboxDeliveryError("invalid_payload", retryable=False)
    return operation_id


def _validated_post_call_reference(
    event: OutboxEvent,
    *,
    topic: str,
    aggregate_type: str,
) -> UUID:
    try:
        call_id = UUID(event.payload["call_id"])
    except (KeyError, TypeError, ValueError):
        raise OutboxDeliveryError("invalid_payload", retryable=False) from None
    if (
        event.topic != topic
        or event.aggregate_type != aggregate_type
        or event.aggregate_id != call_id
    ):
        raise OutboxDeliveryError("invalid_payload", retryable=False)
    return call_id
