from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.core.config import Settings, get_settings
from app.core.database import get_session_factory
from app.core.observability import get_observability
from app.core.provider_failures import ProviderFailure
from app.models.outbox_event import OutboxEvent
from app.providers.summaries.gemini import GeminiSummaryProvider
from app.providers.livekit_recording.livekit import LiveKitRecordingProvider
from app.providers.storage.s3 import S3Storage
from app.repositories.call_repository import CallRepository
from app.repositories.message_repository import MessageRepository
from app.services.livekit_recording_service import LiveKitRecordingService
from app.services.summary_service import SummaryService
from app.workers.outbox.failures import (
    OutboxDeliveryError,
    provider_failure_delivery_error,
)
from app.workers.outbox._livekit_client import (
    LiveKitClientConfigurationError,
    require_livekit_client_config,
)
from app.workers.outbox._owned_resources import operation_owned_resources


async def deliver_summary_generate(
    ctx: dict[str, Any],
    event: OutboxEvent,
) -> None:
    call_id = _validated_post_call_reference(
        event,
        topic="summary.generate",
        aggregate_type="call-summary",
    )
    session_factory = ctx.get("session_factory") or get_session_factory()
    settings = get_settings()
    observability = ctx.get("observability") or get_observability()
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
        async with operation_owned_resources(operation="deliver_summary_generate") as own:
            provider = ctx.get("summary_provider")
            if provider is None:
                provider = GeminiSummaryProvider(
                    api_key=settings.gemini_api_key,
                    model=settings.summary_model,
                    observability=observability,
                )
                own(provider)
            try:
                structured = await provider.generate_summary(transcript)
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
    ctx: dict[str, Any],
    *,
    settings: Settings,
    observability,
    own,
):
    reconciler = ctx.get("recording_reconciler")
    if reconciler is not None:
        return reconciler

    from app.workers.outbox.recording_reconciliation import RecordingReconciler

    session_factory = ctx.get("session_factory") or get_session_factory()
    provider = ctx.get("livekit_recording_provider")
    if provider is None:
        from livekit import api

        try:
            livekit_config = require_livekit_client_config(settings)
        except LiveKitClientConfigurationError:
            raise ProviderFailure(
                provider="livekit",
                operation="list_recording_egresses",
                disposition="terminal",
                error_class="validation",
            ) from None
        livekit_api = own(
            api.LiveKitAPI(
                url=livekit_config.url,
                api_key=livekit_config.api_key,
                api_secret=livekit_config.api_secret,
            )
        )
        provider = LiveKitRecordingService(
            LiveKitRecordingProvider(
                egress_client=livekit_api.egress,
                bucket_name=settings.storage_bucket_name,
                endpoint_url=settings.s3_endpoint_url or "http://minio:9000",
                access_key=settings.s3_access_key,
                secret_key=settings.s3_secret_key,
                region=settings.s3_region,
                observability=observability,
            )
        )
    storage = ctx.get("storage_provider")
    if storage is None:
        storage = own(
            S3Storage(
                bucket_name=settings.storage_bucket_name,
                endpoint_url=settings.s3_endpoint_url or "http://minio:9000",
                access_key=settings.s3_access_key,
                secret_key=settings.s3_secret_key,
                region=settings.s3_region,
                observability=observability,
            )
        )
    now_provider = ctx.get("recording_reconciliation_now") or (
        lambda: datetime.now(UTC)
    )
    return RecordingReconciler(
        session_factory,
        provider,
        storage,
        now_provider=now_provider,
    )


async def deliver_recording_reconcile(
    ctx: dict[str, Any],
    event: OutboxEvent,
) -> None:
    operation_id = _validated_recording_operation_reference(event)
    settings = get_settings()
    observability = ctx.get("observability") or get_observability()
    async with operation_owned_resources(operation="deliver_recording_reconcile") as own:
        try:
            from app.workers.outbox.recording_reconciliation import (
                RECORDING_RECONCILIATION_ERROR_CODES,
            )

            reconciler = build_recording_reconciler(
                ctx,
                settings=settings,
                observability=observability,
                own=own,
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
