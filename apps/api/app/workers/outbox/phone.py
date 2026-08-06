from dataclasses import dataclass
from collections.abc import Callable
from datetime import datetime
from uuid import UUID

from app.core.database import AsyncSessionFactory
from app.core.provider_failures import ProviderFailure
from app.models.agent_config import AgentConfig
from app.models.business_profile import BusinessProfile
from app.models.customer_activation import CustomerActivation
from app.models.outbox_event import OutboxEvent
from app.models.phone_number import PhoneNumber
from app.models.phone_number_provisioning import PhoneNumberProvisioning
from app.models.subscription import Subscription
from app.models.user import User
from app.providers.telephony.base import TelephonyProvider, TelephonyProvisioningPending
from app.repositories.agent_config_repository import AgentConfigRepository
from app.repositories.business_profile_repository import BusinessProfileRepository
from app.repositories.customer_activation_repository import CustomerActivationRepository
from app.repositories.outbox_repository import OutboxRepository
from app.repositories.phone_number_repository import PhoneNumberRepository
from app.repositories.phone_number_provisioning_repository import (
    PhoneNumberProvisioningRepository,
)
from app.repositories.subscription_repository import SubscriptionRepository
from app.repositories.user_repository import UserRepository
from app.repositories.usage_repository import UsageRepository
from app.services.account_access_policy import (
    AccountLifecycleGenerationMismatchError,
    AccountStateBlockedError,
    require_current_account_lifecycle,
)
from app.services.activation_go_live_service import (
    is_current_go_live_event,
    is_go_live_event,
    mark_current_go_live_succeeded,
)
from app.services.customer_readiness_policy import CustomerReadinessResult
from app.services.customer_readiness_service import evaluate_customer_readiness
from app.services.provider_work_policy import UnresolvedProviderWorkError
from app.workers.outbox._account_lifecycle import (
    _require_current_worker_account,
    _validated_lifecycle_generation,
)
from app.workers.outbox.failures import (
    OutboxDeliveryError,
    provider_failure_delivery_error,
)
from app.workers.outbox.phone_provisioning import provision_phone_number


@dataclass(frozen=True)
class _RoutingSnapshot:
    user: User
    activation: CustomerActivation | None
    business_profile: BusinessProfile | None
    phone_number: PhoneNumber
    provisioning: PhoneNumberProvisioning | None
    subscription: Subscription | None
    agent_config: AgentConfig | None
    balance: int
    readiness: CustomerReadinessResult
    current_go_live_attempt: bool
    terminal_after_projection: bool
    provider_number_id: str
    should_enable: bool


@dataclass(frozen=True)
class _PhoneProvisionAdmission:
    provider_operation_key: str | None
    recovery_only: bool


async def deliver_phone_provision(
    event: OutboxEvent,
    *,
    session_factory: AsyncSessionFactory,
    telephony_provider: TelephonyProvider,
    activation_flow_enabled: bool,
    now: Callable[[], datetime],
) -> None:
    user_id = UUID(event.payload["user_id"])
    lifecycle_generation = _validated_lifecycle_generation(event)
    admission = await _phone_provision_admission(
        session_factory,
        user_id,
        event=event,
        lifecycle_generation=lifecycle_generation,
    )
    provider_operation_key = admission.provider_operation_key
    if not provider_operation_key:
        raise OutboxDeliveryError("provider_terminal", retryable=False)
    try:
        await provision_phone_number(
            dict(event.payload),
            session_factory=session_factory,
            telephony_provider=telephony_provider,
            provider_operation_key=provider_operation_key,
        )
    except (AccountStateBlockedError, AccountLifecycleGenerationMismatchError):
        if admission.recovery_only:
            return
        raise OutboxDeliveryError(
            "dispatch_ineligible",
            retryable=False,
        ) from None
    except UnresolvedProviderWorkError:
        raise OutboxDeliveryError(
            "provider_retryable",
            retryable=True,
            exhaustible=False,
        ) from None
    except TelephonyProvisioningPending:
        raise OutboxDeliveryError(
            "provider_retryable",
            retryable=True,
            exhaustible=False,
        ) from None
    except ProviderFailure as exc:
        raise provider_failure_delivery_error(exc) from None
    async with session_factory() as session:
        phone_number = await PhoneNumberRepository(session).get_by_user_id(user_id)
        provisioning = await PhoneNumberProvisioningRepository(session).get_by_user_id(
            user_id
        )
        await session.commit()
    if phone_number is None:
        retryable = bool(
            provisioning is not None
            and (
                provisioning.can_retry
                or provisioning.last_error_reason == "existing_order_pending"
            )
        )
        raise OutboxDeliveryError(
            "provider_retryable" if retryable else "provider_terminal",
            retryable=retryable,
        )
    await deliver_phone_routing(
        event,
        session_factory=session_factory,
        telephony_provider=telephony_provider,
        activation_flow_enabled=activation_flow_enabled,
        now=now,
    )


async def _phone_provision_admission(
    session_factory,
    user_id: UUID,
    *,
    event: OutboxEvent,
    lifecycle_generation: int,
) -> _PhoneProvisionAdmission:
    async with session_factory() as session:
        user = await UserRepository(session).get_by_id_for_update(user_id)
        if user is None:
            await session.rollback()
            raise OutboxDeliveryError(
                "dispatch_ineligible",
                retryable=False,
            )
        provisioning = await PhoneNumberProvisioningRepository(
            session
        ).get_by_user_id_for_update(user_id)
        provider_operation_key = (
            provisioning.provider_operation_key if provisioning is not None else None
        )
        try:
            require_current_account_lifecycle(
                user,
                lifecycle_generation=lifecycle_generation,
            )
        except (AccountStateBlockedError, AccountLifecycleGenerationMismatchError):
            recovery_only = bool(
                provisioning is not None
                and provisioning.status == "running"
                and provider_operation_key is not None
                and _event_matches_provider_operation(
                    event,
                    user_id=user_id,
                    provider_operation_key=provider_operation_key,
                )
            )
            if not recovery_only:
                await session.rollback()
                raise OutboxDeliveryError(
                    "dispatch_ineligible",
                    retryable=False,
                ) from None
            await session.commit()
            return _PhoneProvisionAdmission(
                provider_operation_key=provider_operation_key,
                recovery_only=True,
            )
        await session.commit()
        return _PhoneProvisionAdmission(
            provider_operation_key=provider_operation_key,
            recovery_only=False,
        )


def _event_matches_provider_operation(
    event: OutboxEvent,
    *,
    user_id: UUID,
    provider_operation_key: str,
) -> bool:
    if (
        event.topic != "phone.provision"
        or event.aggregate_type != "user"
        or event.aggregate_id != user_id
    ):
        return False
    if event.idempotency_key == provider_operation_key:
        return True
    attempt_prefix = f"{provider_operation_key}:attempt:"
    attempt = event.idempotency_key.removeprefix(attempt_prefix)
    return (
        event.idempotency_key.startswith(attempt_prefix)
        and attempt.isascii()
        and attempt.isdecimal()
        and int(attempt) >= 1
    )


async def deliver_phone_routing(
    event: OutboxEvent,
    *,
    session_factory: AsyncSessionFactory,
    telephony_provider: TelephonyProvider,
    activation_flow_enabled: bool,
    now: Callable[[], datetime],
) -> None:
    user_id = UUID(event.payload["user_id"])
    lifecycle_generation = (
        _validated_lifecycle_generation(event)
        if event.topic in {"phone.provision", "phone.enable"}
        else None
    )
    if lifecycle_generation is not None:
        await _require_current_worker_account(
            session_factory,
            user_id,
            lifecycle_generation=lifecycle_generation,
        )
    provider = telephony_provider
    routing_target = event.routing_target_provider_number_id

    try:
        async with session_factory() as session:
            snapshot = await _routing_snapshot(
                session,
                user_id,
                event=event,
                activation_flow_enabled=activation_flow_enabled,
            )
            await session.commit()
    except OutboxDeliveryError:
        if routing_target is not None:
            await _compensate_provider_enable(
                provider,
                provider_number_id=routing_target,
            )
            await _clear_routing_target(
                session_factory,
                event=event,
                provider_number_id=routing_target,
            )
        raise

    reconciled_connection_name: str | None = None
    if routing_target is not None and (
        snapshot is None
        or not snapshot.should_enable
        or snapshot.provider_number_id != routing_target
    ):
        reconciled_current_disable = bool(
            snapshot is not None
            and not snapshot.should_enable
            and snapshot.provider_number_id == routing_target
        )
        await _compensate_provider_enable(
            provider,
            provider_number_id=routing_target,
        )
        if snapshot is not None:
            await _persist_phone_projection(
                session_factory,
                user_id=user_id,
                phone_number_id=snapshot.phone_number.id,
                provider_number_id=routing_target,
                provider_connection_name="app-disabled",
            )
        await _clear_routing_target(
            session_factory,
            event=event,
            provider_number_id=routing_target,
        )
        routing_target = None
        if reconciled_current_disable:
            reconciled_connection_name = "app-disabled"
        else:
            async with session_factory() as session:
                snapshot = await _routing_snapshot(
                    session,
                    user_id,
                    event=event,
                    activation_flow_enabled=activation_flow_enabled,
                )
                await session.commit()

    if snapshot is None:
        return
    desired_connection_name = "app-active" if snapshot.should_enable else "app-disabled"

    if (
        reconciled_connection_name is None
        and snapshot.should_enable
        and routing_target is None
    ):
        await _set_routing_target(
            session_factory,
            event=event,
            provider_number_id=snapshot.provider_number_id,
        )
        routing_target = snapshot.provider_number_id

    if reconciled_connection_name is not None:
        provider_connection_name = reconciled_connection_name
    else:
        try:
            if snapshot.should_enable:
                assert routing_target == snapshot.provider_number_id
                if lifecycle_generation is not None:
                    await _require_current_worker_account(
                        session_factory,
                        user_id,
                        lifecycle_generation=lifecycle_generation,
                    )
                provider_connection_name = await provider.enable_number(
                    provider_number_id=routing_target
                )
            else:
                provider_connection_name = await provider.disable_number(
                    provider_number_id=snapshot.provider_number_id
                )
        except ProviderFailure as exc:
            if snapshot.terminal_after_projection:
                raise OutboxDeliveryError(
                    "provider_retryable",
                    retryable=True,
                    exhaustible=False,
                ) from None
            raise provider_failure_delivery_error(exc) from None
    if provider_connection_name != desired_connection_name:
        raise OutboxDeliveryError(
            "provider_retryable",
            retryable=True,
            exhaustible=not snapshot.terminal_after_projection,
        )

    try:
        async with session_factory() as session:
            current = await _routing_snapshot(
                session,
                user_id,
                event=event,
                activation_flow_enabled=activation_flow_enabled,
            )
            stable_projection = bool(
                current is not None
                and current.phone_number.id == snapshot.phone_number.id
                and current.provider_number_id == snapshot.provider_number_id
                and current.should_enable == snapshot.should_enable
            )
            if not stable_projection:
                await session.rollback()
            else:
                assert current is not None
                phone_number = current.phone_number
                phone_number.provider_connection_name = provider_connection_name
                phone_number.is_active = provider_connection_name == "app-active"
                readiness = evaluate_customer_readiness(
                    user=current.user,
                    subscription=current.subscription,
                    balance=current.balance,
                    phone_number=phone_number,
                    provisioning=current.provisioning,
                    agent_config=current.agent_config,
                    business_profile=current.business_profile,
                    activation=current.activation,
                    activation_required=activation_flow_enabled,
                    go_live_activated_override=(
                        True if current.current_go_live_attempt else None
                    ),
                )
                if (
                    provider_connection_name == "app-active"
                    and current.current_go_live_attempt
                    and current.activation is not None
                    and readiness.can_route
                ):
                    await mark_current_go_live_succeeded(
                        session,
                        event=event,
                        activation=current.activation,
                        succeeded_at=now(),
                    )
                terminal_after_projection = current.terminal_after_projection
                await session.commit()
                if terminal_after_projection:
                    raise OutboxDeliveryError(
                        "dispatch_ineligible",
                        retryable=False,
                    )
                if routing_target is not None:
                    await _clear_routing_target(
                        session_factory,
                        event=event,
                        provider_number_id=routing_target,
                    )
                return
    except OutboxDeliveryError:
        if provider_connection_name != "app-active":
            raise
        await _persist_phone_projection(
            session_factory,
            user_id=user_id,
            phone_number_id=snapshot.phone_number.id,
            provider_number_id=snapshot.provider_number_id,
            provider_connection_name="app-active",
        )
        await _compensate_provider_enable(
            provider,
            provider_number_id=snapshot.provider_number_id,
        )
        await _persist_phone_projection(
            session_factory,
            user_id=user_id,
            phone_number_id=snapshot.phone_number.id,
            provider_number_id=snapshot.provider_number_id,
            provider_connection_name="app-disabled",
        )
        if routing_target is not None:
            await _clear_routing_target(
                session_factory,
                event=event,
                provider_number_id=routing_target,
            )
        raise

    await _persist_phone_projection(
        session_factory,
        user_id=user_id,
        phone_number_id=snapshot.phone_number.id,
        provider_number_id=snapshot.provider_number_id,
        provider_connection_name=provider_connection_name,
    )
    if provider_connection_name != "app-active":
        raise OutboxDeliveryError("provider_retryable", retryable=True)

    await _compensate_provider_enable(
        provider,
        provider_number_id=snapshot.provider_number_id,
    )
    await _persist_phone_projection(
        session_factory,
        user_id=user_id,
        phone_number_id=snapshot.phone_number.id,
        provider_number_id=snapshot.provider_number_id,
        provider_connection_name="app-disabled",
    )
    if routing_target is not None:
        await _clear_routing_target(
            session_factory,
            event=event,
            provider_number_id=routing_target,
        )

    async with session_factory() as session:
        current = await _routing_snapshot(
            session,
            user_id,
            event=event,
            activation_flow_enabled=activation_flow_enabled,
        )
        if current is None or current.phone_number.id != snapshot.phone_number.id:
            await session.commit()
            return
        terminal_after_projection = current.terminal_after_projection
        should_enable = current.should_enable
        await session.commit()
    if terminal_after_projection:
        raise OutboxDeliveryError("dispatch_ineligible", retryable=False)
    if should_enable:
        raise OutboxDeliveryError("provider_retryable", retryable=True)


async def _set_routing_target(
    session_factory,
    *,
    event: OutboxEvent,
    provider_number_id: str,
) -> None:
    async with session_factory() as session:
        stored = await OutboxRepository(session).set_routing_target(
            event_id=event.id,
            attempt_count=event.attempt_count,
            provider_number_id=provider_number_id,
        )
        await session.commit()
    if not stored:
        raise OutboxDeliveryError("provider_retryable", retryable=True)


async def _clear_routing_target(
    session_factory,
    *,
    event: OutboxEvent,
    provider_number_id: str,
) -> None:
    async with session_factory() as session:
        cleared = await OutboxRepository(session).clear_routing_target(
            event_id=event.id,
            attempt_count=event.attempt_count,
            provider_number_id=provider_number_id,
        )
        await session.commit()
    if not cleared:
        raise OutboxDeliveryError(
            "provider_retryable",
            retryable=True,
            exhaustible=False,
        )


async def _compensate_provider_enable(provider, *, provider_number_id: str) -> None:
    try:
        connection_name = await provider.disable_number(
            provider_number_id=provider_number_id
        )
    except ProviderFailure:
        # The provider is known to have accepted the enable operation. Keep the
        # durable event retryable until disable is confirmed, even if the
        # provider classifies an individual compensation attempt as terminal.
        raise OutboxDeliveryError(
            "provider_retryable",
            retryable=True,
            exhaustible=False,
        ) from None
    if connection_name != "app-disabled":
        raise OutboxDeliveryError(
            "provider_retryable",
            retryable=True,
            exhaustible=False,
        )


async def _persist_phone_projection(
    session_factory,
    *,
    user_id: UUID,
    phone_number_id: UUID,
    provider_number_id: str,
    provider_connection_name: str,
) -> None:
    async with session_factory() as session:
        user = await UserRepository(session).get_by_id_for_update(user_id)
        if user is None:
            await session.commit()
            return
        phone_number = await PhoneNumberRepository(session).get_by_id_for_update(
            phone_number_id
        )
        if (
            phone_number is not None
            and phone_number.provider_number_id == provider_number_id
        ):
            phone_number.provider_connection_name = provider_connection_name
            phone_number.is_active = provider_connection_name == "app-active"
        await session.commit()


async def _routing_snapshot(
    session,
    user_id: UUID,
    *,
    event: OutboxEvent | None = None,
    activation_flow_enabled: bool,
) -> _RoutingSnapshot | None:
    user = await UserRepository(session).get_by_id_for_update(user_id)
    if user is None:
        return None
    activation = None
    business_profile = None
    if activation_flow_enabled:
        activation = await CustomerActivationRepository(
            session
        ).get_by_user_id_for_update(user_id)
        business_profile = await BusinessProfileRepository(
            session
        ).get_by_user_id_for_update(user_id)
    phone_number = await PhoneNumberRepository(session).get_by_user_id_for_update(
        user_id
    )
    if phone_number is None:
        if (
            event is not None
            and activation_flow_enabled
            and is_current_go_live_event(event, activation)
        ):
            raise OutboxDeliveryError("dispatch_ineligible", retryable=False)
        return None
    if not phone_number.provider_number_id:
        raise OutboxDeliveryError("provider_terminal", retryable=False)
    provisioning = await PhoneNumberProvisioningRepository(
        session
    ).get_by_user_id_for_update(user_id)
    subscription = await SubscriptionRepository(session).get_by_user_id_for_update(
        user_id
    )
    agent_config = await AgentConfigRepository(session).get_by_user_id_for_update(
        user_id
    )
    balance = await UsageRepository(session).get_current_balance(user_id=user_id)
    readiness = evaluate_customer_readiness(
        user=user,
        subscription=subscription,
        balance=balance,
        phone_number=phone_number,
        provisioning=provisioning,
        agent_config=agent_config,
        business_profile=business_profile,
        activation=activation,
        activation_required=activation_flow_enabled,
    )
    current_go_live_attempt = bool(
        event is not None
        and activation_flow_enabled
        and is_current_go_live_event(event, activation)
    )
    if event is not None and activation_flow_enabled and event.topic == "phone.enable":
        pending_go_live = bool(
            activation is not None
            and activation.go_live_requested_at is not None
            and activation.go_live_approved_at is not None
            and activation.activated_at is None
        )
        if is_go_live_event(event) and not current_go_live_attempt:
            return None
        if pending_go_live and not current_go_live_attempt:
            return None
    terminal_after_projection = bool(
        current_go_live_attempt and not readiness.should_enable_phone
    )
    return _RoutingSnapshot(
        user=user,
        activation=activation,
        business_profile=business_profile,
        phone_number=phone_number,
        provisioning=provisioning,
        subscription=subscription,
        agent_config=agent_config,
        balance=balance,
        readiness=readiness,
        current_go_live_attempt=current_go_live_attempt,
        terminal_after_projection=terminal_after_projection,
        provider_number_id=phone_number.provider_number_id,
        should_enable=(
            False if terminal_after_projection else readiness.should_enable_phone
        ),
    )
