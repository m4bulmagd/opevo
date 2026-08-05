from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from presvo_contracts import (
    VERIFICATION_MESSAGE,
    ContractError,
    ForwardingVerificationDispatch,
    create_contract,
    dump_contract_json,
    parse_dispatch,
)

from app.core.config import get_settings
from app.core.dispatch_token import DispatchTokenConfigurationError
from app.core.observability import get_observability
from app.core.verification_token import (
    VerificationTokenError,
    create_verification_token,
)
from app.core.database import get_session_factory
from app.core.provider_failures import ProviderFailure
from app.models.customer_activation import CustomerActivation
from app.models.outbox_event import OutboxEvent
from app.providers.livekit_dispatch.base import LiveKitDispatch
from app.providers.livekit_dispatch.livekit import (
    LiveKitDispatchAPIProvider,
    LiveKitDispatchConfigurationError,
)
from app.providers.summaries.gemini import GeminiSummaryProvider
from app.repositories.call_repository import CallRepository
from app.repositories.customer_activation_repository import (
    CustomerActivationRepository,
)
from app.repositories.message_repository import MessageRepository
from app.repositories.user_repository import UserRepository
from app.services.livekit_dispatch_lock import verification_dispatch_lock
from app.services.livekit_recording_service import LiveKitRecordingService
from app.services.forwarding_verification_service import COMPLETION_GRACE, as_utc
from app.services.summary_service import SummaryService
from app.workers.outbox.failures import (
    OutboxDeliveryError,
    provider_failure_delivery_error,
)
from app.workers.outbox.account_deactivation import deliver_account_deactivation
from app.workers.outbox._account_lifecycle import (
    _require_current_worker_account,
)
from app.workers.outbox.phone import deliver_phone_provision, deliver_phone_routing
from app.workers.outbox.provider_cleanup import deliver_provider_cleanup
from app.workers.outbox.customer_dispatch import deliver_livekit_dispatch


@dataclass(frozen=True)
class _VerificationDispatchSnapshot:
    activation_id: UUID
    user_id: UUID
    session_id: str
    room_name: str
    worker_name: str
    metadata: str
    persisted_dispatch_id: str | None


def _parse_verification_dispatch_metadata(
    metadata: object,
) -> ForwardingVerificationDispatch | None:
    try:
        parsed = parse_dispatch(metadata)
    except ContractError:
        return None
    if not isinstance(parsed, ForwardingVerificationDispatch):
        return None
    return parsed


async def deliver_livekit_verification_dispatch(
    ctx: dict[str, Any],
    event: OutboxEvent,
) -> None:
    activation_id, session_id, room_name, lifecycle_generation = (
        _validated_verification_dispatch_reference(event)
    )
    session_factory = ctx.get("session_factory") or get_session_factory()
    now_provider = ctx.get("verification_now") or (lambda: datetime.now(UTC))

    async with verification_dispatch_lock(session_factory, activation_id):
        snapshot = await _verification_dispatch_snapshot(
            session_factory,
            activation_id=activation_id,
            session_id=session_id,
            room_name=room_name,
            now=now_provider(),
        )
        provider = ctx.get("livekit_dispatch_provider")
        if provider is None:
            provider = LiveKitDispatchAPIProvider()
        await _require_current_worker_account(
            session_factory,
            snapshot.user_id,
            lifecycle_generation=lifecycle_generation,
        )

        try:
            dispatches = await provider.list_dispatches(room_name=snapshot.room_name)
        except LiveKitDispatchConfigurationError:
            raise OutboxDeliveryError(
                "dispatch_configuration",
                retryable=False,
            ) from None
        except ProviderFailure as error:
            raise provider_failure_delivery_error(error) from error
        await _require_current_worker_account(
            session_factory,
            snapshot.user_id,
            lifecycle_generation=lifecycle_generation,
        )

        dispatch = _reconcile_verification_dispatches(snapshot, dispatches)
        if dispatch is None:
            if snapshot.persisted_dispatch_id is not None:
                raise OutboxDeliveryError(
                    "dispatch_conflict",
                    retryable=False,
                )
            await _require_current_worker_account(
                session_factory,
                snapshot.user_id,
                lifecycle_generation=lifecycle_generation,
            )
            try:
                created_dispatch = await provider.create_dispatch(
                    agent_name=snapshot.worker_name,
                    room_name=snapshot.room_name,
                    metadata=snapshot.metadata,
                )
            except LiveKitDispatchConfigurationError:
                raise OutboxDeliveryError(
                    "dispatch_configuration",
                    retryable=False,
                ) from None
            except ProviderFailure as error:
                if not error.retryable:
                    raise provider_failure_delivery_error(error) from error
                try:
                    dispatches = await provider.list_dispatches(
                        room_name=snapshot.room_name
                    )
                except ProviderFailure as list_error:
                    raise provider_failure_delivery_error(list_error) from list_error
                await _require_current_worker_account(
                    session_factory,
                    snapshot.user_id,
                    lifecycle_generation=lifecycle_generation,
                )
                dispatch = _reconcile_verification_dispatches(
                    snapshot,
                    dispatches,
                )
                if dispatch is None:
                    raise OutboxDeliveryError(
                        "provider_retryable",
                        retryable=True,
                    ) from None
            else:
                dispatch = _reconcile_verification_dispatches(
                    snapshot,
                    [created_dispatch],
                )

        if dispatch is None:
            raise OutboxDeliveryError(
                "provider_retryable",
                retryable=True,
            )
        await _persist_verification_dispatch_identity(
            session_factory,
            activation_id=activation_id,
            session_id=session_id,
            dispatch_id=dispatch.id,
        )


def _validated_verification_dispatch_reference(
    event: OutboxEvent,
) -> tuple[UUID, str, str, int]:
    try:
        if set(event.payload) != {
            "activation_id",
            "session_id",
            "room_name",
            "lifecycle_generation",
        }:
            raise ValueError
        activation_id = UUID(event.payload["activation_id"])
        session_id = str(UUID(event.payload["session_id"]))
        room_name = event.payload["room_name"]
        lifecycle_generation = event.payload["lifecycle_generation"]
        if not isinstance(room_name, str) or not room_name:
            raise ValueError
        if type(lifecycle_generation) is not int or lifecycle_generation < 1:
            raise ValueError
    except (KeyError, TypeError, ValueError, AttributeError):
        raise OutboxDeliveryError(
            "dispatch_configuration",
            retryable=False,
        ) from None
    if (
        event.topic != "livekit.verification_dispatch"
        or event.aggregate_type != "forwarding-verification"
        or event.aggregate_id != activation_id
        or event.idempotency_key != f"livekit.verification_dispatch:{session_id}"
    ):
        raise OutboxDeliveryError(
            "dispatch_configuration",
            retryable=False,
        )
    return activation_id, session_id, room_name, lifecycle_generation


async def _verification_dispatch_snapshot(
    session_factory,
    *,
    activation_id: UUID,
    session_id: str,
    room_name: str,
    now: datetime,
) -> _VerificationDispatchSnapshot:
    async with session_factory() as session:
        resolved_activation = await session.get(CustomerActivation, activation_id)
        if resolved_activation is None:
            await session.rollback()
            raise OutboxDeliveryError(
                "dispatch_configuration",
                retryable=False,
            )
        user_id = resolved_activation.user_id
        user = await UserRepository(session).get_by_id_for_update(user_id)
        activation = await CustomerActivationRepository(
            session
        ).get_by_user_id_for_update(user_id)
        if (
            user is None
            or user.status != "active"
            or activation is None
            or activation.id != activation_id
            or activation.verification_status != "claimed"
            or activation.verification_session_id != session_id
            or activation.verification_claimed_at is None
            or activation.verification_window_started_at is None
            or activation.verification_window_expires_at is None
            or as_utc(now) < as_utc(activation.verification_window_started_at)
            or as_utc(now)
            >= as_utc(activation.verification_window_expires_at) + COMPLETION_GRACE
        ):
            await session.rollback()
            raise OutboxDeliveryError(
                "dispatch_ineligible",
                retryable=False,
            )

        worker_name = get_settings().livekit_agent_name.strip()
        if not worker_name:
            await session.rollback()
            raise OutboxDeliveryError(
                "dispatch_configuration",
                retryable=False,
            )
        try:
            metadata = dump_contract_json(
                create_contract(
                    ForwardingVerificationDispatch,
                    job_type="forwarding_verification",
                    verification_session_id=session_id,
                    user_id=user_id,
                    agent_identity=_verification_agent_identity(session_id),
                    completion_token=create_verification_token(
                        session_id=session_id,
                        user_id=str(user_id),
                    ),
                    message=VERIFICATION_MESSAGE,
                    tts_provider="speechmatics",
                )
            )
        except (
            DispatchTokenConfigurationError,
            VerificationTokenError,
            ContractError,
        ):
            await session.rollback()
            raise OutboxDeliveryError(
                "dispatch_configuration",
                retryable=False,
            ) from None

        snapshot = _VerificationDispatchSnapshot(
            activation_id=activation_id,
            user_id=user_id,
            session_id=session_id,
            room_name=room_name,
            worker_name=worker_name,
            metadata=metadata,
            persisted_dispatch_id=activation.verification_dispatch_id,
        )
        await session.commit()
        return snapshot



def _reconcile_verification_dispatches(
    snapshot: _VerificationDispatchSnapshot,
    dispatches: list[LiveKitDispatch],
) -> LiveKitDispatch | None:
    named_dispatches = [
        dispatch for dispatch in dispatches if dispatch.agent_name.strip()
    ]
    expected_session_id = UUID(snapshot.session_id)
    matches: list[LiveKitDispatch] = []
    for dispatch in named_dispatches:
        metadata = _parse_verification_dispatch_metadata(dispatch.metadata)
        if (
            dispatch.agent_name == snapshot.worker_name
            and dispatch.room == snapshot.room_name
            and metadata is not None
            and metadata.verification_session_id == expected_session_id
            and metadata.user_id == snapshot.user_id
            and metadata.agent_identity == _verification_agent_identity(
                snapshot.session_id
            )
        ):
            matches.append(dispatch)

    if not named_dispatches:
        return None
    if len(named_dispatches) == 1 and len(matches) == 1 and matches[0].id:
        if (
            snapshot.persisted_dispatch_id is not None
            and matches[0].id != snapshot.persisted_dispatch_id
        ):
            raise OutboxDeliveryError(
                "dispatch_conflict",
                retryable=False,
            )
        return matches[0]
    raise OutboxDeliveryError(
        "dispatch_conflict",
        retryable=False,
    )


def _verification_agent_identity(session_id: str) -> str:
    return f"agent-verification-{session_id}"


async def _persist_verification_dispatch_identity(
    session_factory,
    *,
    activation_id: UUID,
    session_id: str,
    dispatch_id: str,
) -> None:
    if not dispatch_id:
        raise OutboxDeliveryError(
            "dispatch_conflict",
            retryable=False,
        )
    async with session_factory() as session:
        resolved_activation = await session.get(CustomerActivation, activation_id)
        if resolved_activation is None:
            await session.rollback()
            raise OutboxDeliveryError(
                "dispatch_configuration",
                retryable=False,
            )
        user_id = resolved_activation.user_id
        user = await UserRepository(session).get_by_id_for_update(user_id)
        activation_repository = CustomerActivationRepository(session)
        activation = await activation_repository.get_by_user_id_for_update(user_id)
        if (
            user is None
            or user.status != "active"
            or activation is None
            or activation.id != activation_id
            or activation.verification_session_id != session_id
            or activation.verification_status not in {"claimed", "succeeded"}
        ):
            await session.rollback()
            raise OutboxDeliveryError(
                "dispatch_conflict",
                retryable=False,
            )
        if activation.verification_dispatch_id not in (None, dispatch_id):
            await session.rollback()
            raise OutboxDeliveryError(
                "dispatch_conflict",
                retryable=False,
            )
        await activation_repository.set_verification_dispatch_id(
            activation,
            dispatch_id=dispatch_id,
        )
        await session.commit()


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
        provider = ctx.get("summary_provider") or GeminiSummaryProvider()
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


def build_recording_reconciler(ctx: dict[str, Any]):
    reconciler = ctx.get("recording_reconciler")
    if reconciler is not None:
        return reconciler

    from app.providers.storage.s3 import get_s3_storage
    from app.workers.jobs.recording_reconciliation import RecordingReconciler

    session_factory = ctx.get("session_factory") or get_session_factory()
    provider = ctx.get("livekit_recording_provider") or LiveKitRecordingService()
    storage = ctx.get("storage_provider") or get_s3_storage()
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
    observability = ctx.get("observability") or get_observability()
    try:
        from app.workers.jobs.recording_reconciliation import (
            RECORDING_RECONCILIATION_ERROR_CODES,
        )

        reconciler = build_recording_reconciler(ctx)
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


DEFAULT_OUTBOX_HANDLERS = {
    "account.deactivate": deliver_account_deactivation,
    "provider.cleanup": deliver_provider_cleanup,
    "phone.provision": deliver_phone_provision,
    "phone.enable": deliver_phone_routing,
    "phone.disable": deliver_phone_routing,
    "livekit.dispatch": deliver_livekit_dispatch,
    "livekit.verification_dispatch": deliver_livekit_verification_dispatch,
    "summary.generate": deliver_summary_generate,
    "recording.reconcile": deliver_recording_reconcile,
}
