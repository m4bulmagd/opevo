from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Callable
from uuid import UUID

from app.core.config import get_settings
from app.core.database import get_session_factory
from app.core.provider_failures import ProviderFailure
from app.core.observability import get_observability
from app.models.account_deactivation_operation import AccountDeactivationOperation
from app.models.outbox_event import OutboxEvent
from app.providers.subscriptions.factory import build_subscription_provider
from app.providers.telephony.factory import create_telephony_provider
from app.repositories.account_deactivation_repository import (
    AccountDeactivationRepository,
)
from app.repositories.call_repository import CallRepository
from app.repositories.customer_activation_repository import (
    CustomerActivationRepository,
)
from app.repositories.phone_number_provisioning_repository import (
    PhoneNumberProvisioningRepository,
)
from app.repositories.phone_number_repository import PhoneNumberRepository
from app.repositories.subscription_repository import SubscriptionRepository
from app.repositories.user_repository import UserRepository
from app.services.subscription_access_policy import SubscriptionAccessPolicy
from app.workers.outbox.failures import OutboxDeliveryError


_RETRYABLE_OPERATION_CODES = frozenset({"provider_retryable", "account_call_draining"})
_SAFE_STEPS = frozenset(
    {
        "disable_routing",
        "cancel_subscription",
        "drain_call",
        "release_number",
        "reset_activation",
        "complete",
    }
)


@dataclass(frozen=True)
class _OperationSnapshot:
    operation_id: UUID
    user_id: UUID
    trigger: str
    requested_at: datetime
    phone_provider_id: str | None
    stripe_subscription_id: str | None


async def deliver_account_deactivation(
    ctx: dict[str, Any],
    event: OutboxEvent,
) -> None:
    operation_id = _validated_operation_id(event)
    session_factory = ctx.get("session_factory") or get_session_factory()
    now_provider: Callable[[], datetime] = ctx.get(
        "account_deactivation_now",
        lambda: datetime.now(UTC),
    )
    telemetry = ctx.get("observability") or get_observability()

    operation = await _begin_attempt(
        session_factory,
        operation_id=operation_id,
        now=now_provider(),
    )
    if operation is None:
        return

    await _disable_routing(
        ctx,
        session_factory=session_factory,
        operation=operation,
        now_provider=now_provider,
        telemetry=telemetry,
    )
    await _cancel_subscription(
        ctx,
        session_factory=session_factory,
        operation=operation,
        now_provider=now_provider,
        telemetry=telemetry,
    )
    await _drain_active_call(
        session_factory=session_factory,
        operation=operation,
        now_provider=now_provider,
        telemetry=telemetry,
    )
    await _release_number(
        ctx,
        session_factory=session_factory,
        operation=operation,
        now_provider=now_provider,
        telemetry=telemetry,
    )
    completed_at = await _reset_activation(
        session_factory=session_factory,
        operation=operation,
        now_provider=now_provider,
        telemetry=telemetry,
    )
    if completed_at is None:
        completed_at = await _complete(
            session_factory=session_factory,
            operation=operation,
            now=now_provider(),
        )
    if completed_at is not None:
        duration = _elapsed_seconds(operation.requested_at, completed_at)
        _safe_telemetry(
            telemetry,
            "record_account_deactivation_completion",
            operation.trigger,
            duration,
        )
        _record_result(
            telemetry,
            trigger=operation.trigger,
            step="complete",
            outcome="success",
            error_class="unknown",
        )


def _validated_operation_id(event: OutboxEvent) -> UUID:
    if not isinstance(event.payload, dict) or set(event.payload) != {"operation_id"}:
        raise OutboxDeliveryError("invalid_payload", retryable=False)
    try:
        operation_id = UUID(event.payload["operation_id"])
    except (KeyError, TypeError, ValueError, AttributeError):
        raise OutboxDeliveryError("invalid_payload", retryable=False) from None
    if (
        event.topic != "account.deactivate"
        or event.aggregate_type != "account-deactivation-operation"
        or event.aggregate_id != operation_id
    ):
        raise OutboxDeliveryError("invalid_payload", retryable=False)
    return operation_id


async def _begin_attempt(
    session_factory,
    *,
    operation_id: UUID,
    now: datetime,
) -> _OperationSnapshot | None:
    async with session_factory() as session:
        operation = await AccountDeactivationRepository(session).get_by_id_for_update(
            operation_id
        )
        if operation is None:
            await session.commit()
            raise OutboxDeliveryError("invalid_payload", retryable=False)
        if operation.completed_at is not None:
            await session.commit()
            return None
        operation.status = "processing"
        operation.attempt_count += 1
        operation.last_reconciled_at = now
        if operation.last_error_code in _RETRYABLE_OPERATION_CODES:
            operation.last_error_code = None
        snapshot = _snapshot(operation)
        await session.commit()
        return snapshot


async def _disable_routing(
    ctx: dict[str, Any],
    *,
    session_factory,
    operation: _OperationSnapshot,
    now_provider: Callable[[], datetime],
    telemetry,
) -> None:
    provider_number_id = await _pending_private_identity(
        session_factory,
        operation.operation_id,
        timestamp_name="routing_disabled_at",
        identity_name="phone_provider_id",
    )
    if provider_number_id is _STEP_COMPLETE:
        return
    if provider_number_id is not None:
        provider = ctx.get("telephony_provider")
        if provider is None:
            provider = create_telephony_provider(get_settings())
        try:
            await provider.disable_number(provider_number_id=provider_number_id)
        except ProviderFailure as error:
            await _handle_provider_error(
                session_factory=session_factory,
                operation=operation,
                step="disable_routing",
                error=error,
                now=now_provider(),
                telemetry=telemetry,
            )
    await _mark_timestamp(
        session_factory,
        operation.operation_id,
        timestamp_name="routing_disabled_at",
        now=now_provider(),
    )
    _record_result(
        telemetry,
        trigger=operation.trigger,
        step="disable_routing",
        outcome="success",
        error_class="unknown",
    )


async def _cancel_subscription(
    ctx: dict[str, Any],
    *,
    session_factory,
    operation: _OperationSnapshot,
    now_provider: Callable[[], datetime],
    telemetry,
) -> None:
    async with session_factory() as session:
        stored = await AccountDeactivationRepository(session).get_by_id_for_update(
            operation.operation_id
        )
        if stored is None:
            await session.commit()
            raise OutboxDeliveryError("invalid_payload", retryable=False)
        if stored.subscription_canceled_at is not None:
            await session.commit()
            return
        subscription = await SubscriptionRepository(session).get_by_user_id(
            stored.user_id
        )
        current_subscription_id = (
            subscription.stripe_subscription_id if subscription is not None else None
        )
        current_status = subscription.status if subscription is not None else None
        stored_subscription_id = stored.stripe_subscription_id
        trigger = stored.trigger
        await session.commit()

    terminal = current_status is None or (
        current_status is not None
        and SubscriptionAccessPolicy.can_replace_subscription(current_status)
    )
    matching = (
        current_subscription_id is None
        or current_subscription_id == stored_subscription_id
    )
    if trigger == "subscription_ended":
        if not terminal:
            await _mark_retryable(
                session_factory,
                operation_id=operation.operation_id,
                now=now_provider(),
            )
            _record_result(
                telemetry,
                trigger=operation.trigger,
                step="cancel_subscription",
                outcome="retry",
                error_class="unavailable",
            )
            raise OutboxDeliveryError(
                "provider_retryable",
                retryable=True,
                exhaustible=False,
            )
        await _mark_timestamp(
            session_factory,
            operation.operation_id,
            timestamp_name="subscription_canceled_at",
            now=now_provider(),
        )
        _record_result(
            telemetry,
            trigger=operation.trigger,
            step="cancel_subscription",
            outcome="success",
            error_class="unknown",
        )
        return

    if terminal:
        await _mark_timestamp(
            session_factory,
            operation.operation_id,
            timestamp_name="subscription_canceled_at",
            now=now_provider(),
        )
        _record_result(
            telemetry,
            trigger=operation.trigger,
            step="cancel_subscription",
            outcome="success",
            error_class="unknown",
        )
        return
    if stored_subscription_id is None or not matching:
        await _mark_attention_and_raise(
            session_factory=session_factory,
            operation=operation,
            step="cancel_subscription",
            code="subscription_contract",
            error_class="conflict",
            now=now_provider(),
            telemetry=telemetry,
        )
    assert stored_subscription_id is not None

    provider = ctx.get("subscription_provider")
    if provider is None:
        provider = build_subscription_provider(get_settings())
    try:
        await provider.cancel_immediately(stored_subscription_id)
    except ProviderFailure as error:
        await _handle_provider_error(
            session_factory=session_factory,
            operation=operation,
            step="cancel_subscription",
            error=error,
            now=now_provider(),
            telemetry=telemetry,
        )

    mismatch = False
    async with session_factory() as session:
        subscription = await SubscriptionRepository(session).get_by_user_id_for_update(
            operation.user_id
        )
        stored = await AccountDeactivationRepository(session).get_by_id_for_update(
            operation.operation_id
        )
        if stored is None:
            await session.commit()
            raise OutboxDeliveryError("invalid_payload", retryable=False)
        if stored.subscription_canceled_at is not None:
            await session.commit()
            return
        if (
            subscription is not None
            and subscription.stripe_subscription_id != stored_subscription_id
        ):
            mismatch = True
        else:
            effective_at = now_provider()
            if subscription is not None:
                subscription.status = "canceled"
                subscription.cancel_at_period_end = False
                subscription.cancellation_effective_at = effective_at
            stored.subscription_canceled_at = effective_at
        await session.commit()
    if mismatch:
        await _mark_attention_and_raise(
            session_factory=session_factory,
            operation=operation,
            step="cancel_subscription",
            code="subscription_contract",
            error_class="conflict",
            now=now_provider(),
            telemetry=telemetry,
        )
    _record_result(
        telemetry,
        trigger=operation.trigger,
        step="cancel_subscription",
        outcome="success",
        error_class="unknown",
    )


async def _drain_active_call(
    *,
    session_factory,
    operation: _OperationSnapshot,
    now_provider: Callable[[], datetime],
    telemetry,
) -> None:
    async with session_factory() as session:
        stored = await AccountDeactivationRepository(session).get_by_id_for_update(
            operation.operation_id
        )
        if stored is None:
            await session.commit()
            raise OutboxDeliveryError("invalid_payload", retryable=False)
        if stored.active_call_drained_at is not None:
            await session.commit()
            return
        user_id = stored.user_id
        await session.commit()
    active = await _has_active_call(
        session_factory,
        user_id=user_id,
    )
    if active:
        await _mark_call_draining(
            session_factory,
            operation=operation,
            now=now_provider(),
            telemetry=telemetry,
        )
    await _mark_timestamp(
        session_factory,
        operation.operation_id,
        timestamp_name="active_call_drained_at",
        now=now_provider(),
    )
    _record_result(
        telemetry,
        trigger=operation.trigger,
        step="drain_call",
        outcome="success",
        error_class="unknown",
    )


async def _release_number(
    ctx: dict[str, Any],
    *,
    session_factory,
    operation: _OperationSnapshot,
    now_provider: Callable[[], datetime],
    telemetry,
) -> None:
    if await _has_active_call(
        session_factory,
        user_id=operation.user_id,
    ):
        await _mark_call_draining(
            session_factory,
            operation=operation,
            now=now_provider(),
            telemetry=telemetry,
        )
    provider_number_id = await _pending_private_identity(
        session_factory,
        operation.operation_id,
        timestamp_name="number_released_at",
        identity_name="phone_provider_id",
    )
    if provider_number_id is _STEP_COMPLETE:
        return
    if provider_number_id is not None:
        provider = ctx.get("telephony_provider")
        if provider is None:
            provider = create_telephony_provider(get_settings())
        try:
            await provider.release_number(provider_number_id=provider_number_id)
        except ProviderFailure as error:
            await _handle_provider_error(
                session_factory=session_factory,
                operation=operation,
                step="release_number",
                error=error,
                now=now_provider(),
                telemetry=telemetry,
            )
    await _mark_timestamp(
        session_factory,
        operation.operation_id,
        timestamp_name="number_released_at",
        now=now_provider(),
    )
    _record_result(
        telemetry,
        trigger=operation.trigger,
        step="release_number",
        outcome="success",
        error_class="unknown",
    )


async def _reset_activation(
    *,
    session_factory,
    operation: _OperationSnapshot,
    now_provider: Callable[[], datetime],
    telemetry,
) -> datetime | None:
    async with session_factory() as session:
        uncommitted = await AccountDeactivationRepository(session).get_by_id(
            operation.operation_id
        )
        if uncommitted is None:
            await session.commit()
            raise OutboxDeliveryError("invalid_payload", retryable=False)
        if uncommitted.activation_reset_at is not None:
            await session.commit()
            return None
        user_id = uncommitted.user_id
        await session.commit()

    if await _has_active_call(
        session_factory,
        user_id=user_id,
    ):
        await _mark_call_draining(
            session_factory,
            operation=operation,
            now=now_provider(),
            telemetry=telemetry,
        )

    await _detach_call_history_phone(
        session_factory,
        user_id=user_id,
    )

    if await _has_active_call(
        session_factory,
        user_id=user_id,
    ):
        await _mark_call_draining(
            session_factory,
            operation=operation,
            now=now_provider(),
            telemetry=telemetry,
        )

    async with session_factory() as session:
        user = await UserRepository(session).get_by_id_for_update(user_id)
        if user is None:
            await session.commit()
            raise OutboxDeliveryError("invalid_payload", retryable=False)
        phone = await PhoneNumberRepository(session).get_by_user_id_for_update(user_id)
        stored = await AccountDeactivationRepository(session).get_by_id_for_update(
            operation.operation_id
        )
        if stored is None:
            await session.commit()
            raise OutboxDeliveryError("invalid_payload", retryable=False)
        if stored.activation_reset_at is not None:
            await session.commit()
            return None
        await PhoneNumberProvisioningRepository(
            session
        ).delete_resolved_for_deactivation(user_id)
        if phone is not None:
            await PhoneNumberRepository(session).delete_for_deactivation(phone)
        await CustomerActivationRepository(session).reset_number_cycle(user_id)
        completed_at = now_provider()
        stored.activation_reset_at = completed_at
        stored.completed_at = completed_at
        stored.status = "completed"
        stored.last_error_code = None
        user.status = "inactive"
        await session.commit()
    _record_result(
        telemetry,
        trigger=operation.trigger,
        step="reset_activation",
        outcome="success",
        error_class="unknown",
    )
    return completed_at


async def _detach_call_history_phone(
    session_factory,
    *,
    user_id: UUID,
) -> None:
    async with session_factory() as session:
        phone = await PhoneNumberRepository(session).get_by_user_id(user_id)
        if phone is not None:
            await CallRepository(session).detach_phone_number(phone.id)
        await session.commit()


async def _has_active_call(
    session_factory,
    *,
    user_id: UUID,
) -> bool:
    async with session_factory() as session:
        active = await CallRepository(session).has_active_by_user_id(user_id)
        await session.commit()
        return active


async def _mark_call_draining(
    session_factory,
    *,
    operation: _OperationSnapshot,
    now: datetime,
    telemetry,
) -> None:
    await _mark_retryable(
        session_factory,
        operation_id=operation.operation_id,
        now=now,
        code="account_call_draining",
    )
    _record_result(
        telemetry,
        trigger=operation.trigger,
        step="drain_call",
        outcome="retry",
        error_class="unavailable",
    )
    raise OutboxDeliveryError(
        "account_call_draining",
        retryable=True,
        exhaustible=False,
    )


async def _complete(
    *,
    session_factory,
    operation: _OperationSnapshot,
    now: datetime,
) -> datetime | None:
    async with session_factory() as session:
        user = await UserRepository(session).get_by_id_for_update(operation.user_id)
        stored = await AccountDeactivationRepository(session).get_by_id_for_update(
            operation.operation_id
        )
        if user is None or stored is None:
            await session.commit()
            raise OutboxDeliveryError("invalid_payload", retryable=False)
        if stored.completed_at is not None:
            await session.commit()
            return None
        if stored.activation_reset_at is None:
            await session.commit()
            raise OutboxDeliveryError("provider_retryable", retryable=True)
        stored.completed_at = now
        stored.status = "completed"
        stored.last_error_code = None
        user.status = "inactive"
        await session.commit()
        return now


_STEP_COMPLETE = object()


async def _pending_private_identity(
    session_factory,
    operation_id: UUID,
    *,
    timestamp_name: str,
    identity_name: str,
):
    async with session_factory() as session:
        operation = await AccountDeactivationRepository(session).get_by_id_for_update(
            operation_id
        )
        if operation is None:
            await session.commit()
            raise OutboxDeliveryError("invalid_payload", retryable=False)
        if getattr(operation, timestamp_name) is not None:
            await session.commit()
            return _STEP_COMPLETE
        identity = getattr(operation, identity_name)
        await session.commit()
        return identity


async def _mark_timestamp(
    session_factory,
    operation_id: UUID,
    *,
    timestamp_name: str,
    now: datetime,
) -> None:
    async with session_factory() as session:
        operation = await AccountDeactivationRepository(session).get_by_id_for_update(
            operation_id
        )
        if operation is None:
            await session.commit()
            raise OutboxDeliveryError("invalid_payload", retryable=False)
        if getattr(operation, timestamp_name) is None:
            setattr(operation, timestamp_name, now)
        operation.last_error_code = None
        await session.commit()


async def _mark_retryable(
    session_factory,
    *,
    operation_id: UUID,
    now: datetime,
    code: str = "provider_retryable",
) -> None:
    async with session_factory() as session:
        operation = await AccountDeactivationRepository(session).get_by_id_for_update(
            operation_id
        )
        if operation is None:
            await session.commit()
            raise OutboxDeliveryError("invalid_payload", retryable=False)
        operation.status = "processing"
        operation.last_error_code = code
        operation.last_reconciled_at = now
        await session.commit()


async def _handle_provider_error(
    *,
    session_factory,
    operation: _OperationSnapshot,
    step: str,
    error: ProviderFailure,
    now: datetime,
    telemetry,
) -> None:
    if error.retryable:
        await _mark_retryable(
            session_factory,
            operation_id=operation.operation_id,
            now=now,
        )
        _record_result(
            telemetry,
            trigger=operation.trigger,
            step=step,
            outcome="retry",
            error_class=error.error_class,
        )
        raise OutboxDeliveryError(
            "provider_retryable",
            retryable=True,
            exhaustible=False,
        ) from None

    if step == "cancel_subscription":
        code = (
            "subscription_authentication"
            if error.error_class == "authentication"
            else "subscription_contract"
        )
    elif error.error_class == "authentication":
        code = "telephony_authentication"
    elif step == "release_number" and error.error_class == "conflict":
        code = "telephony_release_conflict"
    else:
        code = "provider_contract"
    await _mark_attention_and_raise(
        session_factory=session_factory,
        operation=operation,
        step=step,
        code=code,
        error_class=error.error_class,
        now=now,
        telemetry=telemetry,
    )


async def _mark_attention_and_raise(
    *,
    session_factory,
    operation: _OperationSnapshot,
    step: str,
    code: str,
    error_class: str,
    now: datetime,
    telemetry,
) -> None:
    async with session_factory() as session:
        stored = await AccountDeactivationRepository(session).get_by_id_for_update(
            operation.operation_id
        )
        if stored is None:
            await session.commit()
            raise OutboxDeliveryError("invalid_payload", retryable=False)
        stored.status = "attention_required"
        stored.last_error_code = code
        stored.last_reconciled_at = now
        await session.commit()
    _safe_telemetry(
        telemetry,
        "record_account_deactivation_attention",
        operation.trigger,
        step,
        error_class,
    )
    _record_result(
        telemetry,
        trigger=operation.trigger,
        step=step,
        outcome="attention",
        error_class=error_class,
    )
    raise OutboxDeliveryError(code, retryable=False, exhaustible=True) from None


def _snapshot(operation: AccountDeactivationOperation) -> _OperationSnapshot:
    return _OperationSnapshot(
        operation_id=operation.id,
        user_id=operation.user_id,
        trigger=operation.trigger,
        requested_at=operation.requested_at,
        phone_provider_id=operation.phone_provider_id,
        stripe_subscription_id=operation.stripe_subscription_id,
    )


def _record_result(
    telemetry,
    *,
    trigger: str,
    step: str,
    outcome: str,
    error_class: str,
) -> None:
    if step not in _SAFE_STEPS:
        step = "unknown"
    _safe_telemetry(
        telemetry,
        "record_account_deactivation_result",
        trigger,
        step,
        outcome,
        error_class,
    )


def _safe_telemetry(telemetry, method_name: str, *args: object) -> None:
    try:
        getattr(telemetry, method_name)(*args)
    except Exception:
        return


def _elapsed_seconds(start: datetime, end: datetime) -> float:
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    if end.tzinfo is None:
        end = end.replace(tzinfo=UTC)
    return max(0.0, (end - start).total_seconds())
