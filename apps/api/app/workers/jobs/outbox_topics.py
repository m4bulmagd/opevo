from dataclasses import dataclass
from datetime import UTC, datetime
import json
from typing import Any
from uuid import UUID

from app.core.config import get_settings
from app.core.dispatch_token import create_dispatch_token
from app.core.observability import get_observability
from app.core.verification_token import create_verification_token
from app.core.database import get_session_factory
from app.models.agent_config import AgentConfig
from app.models.business_profile import BusinessProfile
from app.models.customer_activation import CustomerActivation
from app.models.outbox_event import OutboxEvent
from app.models.phone_number import PhoneNumber
from app.models.phone_number_provisioning import PhoneNumberProvisioning
from app.models.subscription import Subscription
from app.models.user import User
from app.providers.livekit_dispatch.base import LiveKitDispatch
from app.providers.livekit_dispatch.livekit import LiveKitDispatchAPIProvider
from app.providers.summaries.gemini import GeminiSummaryProvider
from app.providers.telephony.base import TelephonyProviderError
from app.providers.telephony.factory import create_telephony_provider
from app.repositories.agent_config_repository import AgentConfigRepository
from app.repositories.business_profile_repository import BusinessProfileRepository
from app.repositories.call_repository import CallRepository
from app.repositories.customer_activation_repository import (
    CustomerActivationRepository,
)
from app.repositories.message_repository import MessageRepository
from app.repositories.outbox_repository import OutboxRepository
from app.repositories.phone_number_repository import PhoneNumberRepository
from app.repositories.phone_number_provisioning_repository import (
    PhoneNumberProvisioningRepository,
)
from app.repositories.subscription_repository import SubscriptionRepository
from app.repositories.user_repository import UserRepository
from app.repositories.usage_repository import UsageRepository
from app.schemas.livekit import LiveKitDispatchMetadata, VerificationDispatchMetadata
from app.services.activation_go_live_service import (
    is_current_go_live_event,
    is_go_live_event,
    mark_current_go_live_succeeded,
)
from app.services.customer_readiness_policy import CustomerReadinessResult
from app.services.account_access_policy import (
    AccountLifecycleGenerationMismatchError,
    AccountStateBlockedError,
    require_current_account_lifecycle,
)
from app.services.customer_readiness_service import (
    evaluate_customer_readiness,
)
from app.services.livekit_dispatch_service import (
    calculate_allowed_duration,
    expected_agent_identity,
)
from app.services.livekit_dispatch_lock import livekit_dispatch_lock
from app.services.livekit_dispatch_lock import verification_dispatch_lock
from app.services.livekit_recording_service import LiveKitRecordingService
from app.services.provider_work_policy import UnresolvedProviderWorkError
from app.services.forwarding_verification_service import COMPLETION_GRACE, as_utc
from app.services.summary_service import SummaryService
from app.workers.jobs.outbox_delivery import OutboxDeliveryError
from app.workers.jobs.account_deactivation import deliver_account_deactivation
from app.workers.jobs.phone_provisioning import phone_provisioning_job
from app.workers.jobs.provider_cleanup import deliver_provider_cleanup


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
class _DispatchSnapshot:
    call_id: UUID
    user_id: UUID
    agent_config_id: UUID
    room_name: str
    worker_name: str
    metadata: str
    persisted_dispatch_id: str | None


@dataclass(frozen=True)
class _VerificationDispatchSnapshot:
    activation_id: UUID
    user_id: UUID
    session_id: str
    room_name: str
    worker_name: str
    metadata: str
    persisted_dispatch_id: str | None


@dataclass(frozen=True)
class _PhoneProvisionAdmission:
    provider_operation_key: str | None
    recovery_only: bool


async def deliver_phone_provision(
    ctx: dict[str, Any],
    event: OutboxEvent,
) -> None:
    user_id = UUID(event.payload["user_id"])
    lifecycle_generation = _validated_lifecycle_generation(event)
    session_factory = ctx.get("session_factory") or get_session_factory()
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
        await phone_provisioning_job(
            ctx,
            dict(event.payload),
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
    except TelephonyProviderError as exc:
        raise OutboxDeliveryError(
            exc.category,
            retryable=exc.retryable,
        ) from None
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
    await deliver_phone_routing(ctx, event)


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
    ctx: dict[str, Any],
    event: OutboxEvent,
) -> None:
    user_id = UUID(event.payload["user_id"])
    lifecycle_generation = (
        _validated_lifecycle_generation(event)
        if event.topic in {"phone.provision", "phone.enable"}
        else None
    )
    session_factory = ctx.get("session_factory") or get_session_factory()
    if lifecycle_generation is not None:
        await _require_current_worker_account(
            session_factory,
            user_id,
            lifecycle_generation=lifecycle_generation,
        )
    provider = ctx.get("telephony_provider")
    if provider is None:
        provider = create_telephony_provider(get_settings())
    routing_target = event.routing_target_provider_number_id

    try:
        async with session_factory() as session:
            snapshot = await _routing_snapshot(session, user_id, event=event)
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
                snapshot = await _routing_snapshot(session, user_id, event=event)
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
        except TelephonyProviderError as exc:
            if snapshot.terminal_after_projection:
                raise OutboxDeliveryError(
                    "provider_retryable",
                    retryable=True,
                    exhaustible=False,
                ) from None
            raise OutboxDeliveryError(
                exc.category,
                retryable=exc.retryable,
            ) from None
    if provider_connection_name != desired_connection_name:
        raise OutboxDeliveryError(
            "provider_retryable",
            retryable=True,
            exhaustible=not snapshot.terminal_after_projection,
        )

    try:
        async with session_factory() as session:
            current = await _routing_snapshot(session, user_id, event=event)
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
                    activation_required=get_settings().activation_flow_enabled,
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
                    now_provider = ctx.get("routing_now") or (lambda: datetime.now(UTC))
                    await mark_current_go_live_succeeded(
                        session,
                        event=event,
                        activation=current.activation,
                        succeeded_at=now_provider(),
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
        current = await _routing_snapshot(session, user_id, event=event)
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
    except TelephonyProviderError:
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
) -> _RoutingSnapshot | None:
    user = await UserRepository(session).get_by_id_for_update(user_id)
    if user is None:
        return None
    activation_flow_enabled = get_settings().activation_flow_enabled
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


async def deliver_livekit_dispatch(
    ctx: dict[str, Any],
    event: OutboxEvent,
) -> None:
    call_id, lifecycle_generation = _validated_dispatch_reference(event)
    session_factory = ctx.get("session_factory") or get_session_factory()

    async with livekit_dispatch_lock(session_factory, call_id):
        snapshot = await _dispatch_snapshot(session_factory, call_id)
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
        except ValueError:
            raise OutboxDeliveryError(
                "dispatch_configuration",
                retryable=False,
            ) from None
        except Exception:
            raise OutboxDeliveryError(
                "provider_retryable",
                retryable=True,
            ) from None
        await _require_current_worker_account(
            session_factory,
            snapshot.user_id,
            lifecycle_generation=lifecycle_generation,
        )

        dispatch = _reconcile_dispatches(snapshot, dispatches)
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
            except ValueError:
                raise OutboxDeliveryError(
                    "dispatch_configuration",
                    retryable=False,
                ) from None
            except Exception:
                try:
                    dispatches = await provider.list_dispatches(
                        room_name=snapshot.room_name
                    )
                except Exception:
                    raise OutboxDeliveryError(
                        "provider_retryable",
                        retryable=True,
                    ) from None
                await _require_current_worker_account(
                    session_factory,
                    snapshot.user_id,
                    lifecycle_generation=lifecycle_generation,
                )
                dispatch = _reconcile_dispatches(snapshot, dispatches)
                if dispatch is None:
                    raise OutboxDeliveryError(
                        "provider_retryable",
                        retryable=True,
                    ) from None
            else:
                dispatch = _reconcile_dispatches(
                    snapshot,
                    [created_dispatch],
                )

        if dispatch is None:
            raise OutboxDeliveryError(
                "provider_retryable",
                retryable=True,
            )
        await _persist_dispatch_identity(
            session_factory,
            call_id=call_id,
            dispatch_id=dispatch.id,
        )


def _validated_dispatch_reference(event: OutboxEvent) -> tuple[UUID, int]:
    try:
        if set(event.payload) != {"call_id", "lifecycle_generation"}:
            raise ValueError
        call_id = UUID(event.payload["call_id"])
        lifecycle_generation = event.payload["lifecycle_generation"]
        if type(lifecycle_generation) is not int or lifecycle_generation < 1:
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise OutboxDeliveryError(
            "dispatch_configuration",
            retryable=False,
        ) from None
    if event.aggregate_type != "call" or event.aggregate_id != call_id:
        raise OutboxDeliveryError(
            "dispatch_configuration",
            retryable=False,
        )
    return call_id, lifecycle_generation


async def _dispatch_snapshot(session_factory, call_id: UUID) -> _DispatchSnapshot:
    async with session_factory() as session:
        call_repository = CallRepository(session)
        call = await call_repository.get_by_id(call_id)
        if call is None or call.agent_config_id is None or not call.livekit_room_id:
            await session.rollback()
            raise OutboxDeliveryError(
                "dispatch_configuration",
                retryable=False,
            )

        user = await UserRepository(session).get_by_id_for_update(call.user_id)
        if user is None:
            await session.rollback()
            raise OutboxDeliveryError(
                "dispatch_configuration",
                retryable=False,
            )
        await session.refresh(call)

        settings = get_settings()
        activation = None
        business_profile = None
        if settings.activation_flow_enabled:
            activation = await CustomerActivationRepository(
                session
            ).get_by_user_id_for_update(call.user_id)
            business_profile = await BusinessProfileRepository(
                session
            ).get_by_user_id_for_update(call.user_id)
        phone = (
            await PhoneNumberRepository(session).get_by_id_for_update(
                call.phone_number_id
            )
            if call.phone_number_id is not None
            else None
        )
        provisioning = await PhoneNumberProvisioningRepository(
            session
        ).get_by_user_id_for_update(call.user_id)
        subscription = await SubscriptionRepository(session).get_by_user_id_for_update(
            call.user_id
        )
        agent_config = await AgentConfigRepository(session).get_by_user_id_for_update(
            call.user_id
        )
        balance = await UsageRepository(session).get_current_balance(
            user_id=call.user_id
        )
        if subscription is None or agent_config is None:
            await session.rollback()
            raise OutboxDeliveryError(
                "dispatch_ineligible",
                retryable=False,
            )

        called_number_matches = bool(
            phone is not None
            and phone.id == call.phone_number_id
            and phone.user_id == call.user_id
            and bool(phone.e164)
        )
        readiness = evaluate_customer_readiness(
            user=user,
            subscription=subscription,
            balance=balance,
            phone_number=phone,
            provisioning=provisioning,
            agent_config=agent_config,
            business_profile=business_profile,
            activation=activation,
            activation_required=settings.activation_flow_enabled,
        )
        eligible = bool(
            user.status == "active"
            and call.status in {"pending", "connected"}
            and agent_config.id == call.agent_config_id
            and readiness.can_dispatch(called_number_matches=called_number_matches)
        )
        if not eligible:
            await session.rollback()
            raise OutboxDeliveryError(
                "dispatch_ineligible",
                retryable=False,
            )

        try:
            worker_name = settings.livekit_agent_name.strip()
            if not worker_name:
                raise ValueError("LiveKit agent worker name is not configured")
            business_display_name = (agent_config.business_display_name or "").strip()
            if settings.activation_flow_enabled and not business_display_name:
                raise ValueError("Projected business display name is not configured")
            dispatch_token = create_dispatch_token(
                call_id=str(call.id),
                user_id=str(call.user_id),
                agent_config_id=str(agent_config.id),
            )
            metadata = LiveKitDispatchMetadata(
                user_id=str(call.user_id),
                agent_config_id=str(agent_config.id),
                call_id=str(call.id),
                agent_identity=expected_agent_identity(call.id),
                minutes_remaining=balance,
                allowed_duration_seconds=calculate_allowed_duration(
                    minutes_remaining=balance,
                    maximum=settings.max_call_duration_seconds,
                ),
                agent_name=agent_config.agent_name,
                owner_name=(
                    business_display_name
                    or (user.full_name or "").strip()
                    or "the business"
                ),
                owner_context=agent_config.owner_context,
                system_prompt=agent_config.system_prompt,
                knowledge_base=agent_config.knowledge_base,
                pipeline_mode=agent_config.pipeline_mode,
                dispatch_token=dispatch_token,
            ).model_dump_json()
        except Exception:
            await session.rollback()
            raise OutboxDeliveryError(
                "dispatch_configuration",
                retryable=False,
            ) from None

        snapshot = _DispatchSnapshot(
            call_id=call.id,
            user_id=call.user_id,
            agent_config_id=agent_config.id,
            room_name=call.livekit_room_id,
            worker_name=worker_name,
            metadata=metadata,
            persisted_dispatch_id=call.livekit_dispatch_id,
        )
        await session.commit()
        return snapshot


def _reconcile_dispatches(
    snapshot: _DispatchSnapshot,
    dispatches: list[LiveKitDispatch],
) -> LiveKitDispatch | None:
    named_dispatches = [
        dispatch for dispatch in dispatches if dispatch.agent_name.strip()
    ]
    matches: list[LiveKitDispatch] = []
    for dispatch in named_dispatches:
        try:
            metadata = json.loads(dispatch.metadata)
        except (TypeError, ValueError):
            metadata = None
        if (
            dispatch.agent_name == snapshot.worker_name
            and dispatch.room == snapshot.room_name
            and isinstance(metadata, dict)
            and metadata.get("call_id") == str(snapshot.call_id)
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


async def _persist_dispatch_identity(
    session_factory,
    *,
    call_id: UUID,
    dispatch_id: str,
) -> None:
    if not dispatch_id:
        raise OutboxDeliveryError(
            "dispatch_conflict",
            retryable=False,
        )
    async with session_factory() as session:
        call = await CallRepository(session).get_by_id_for_update(call_id)
        if call is None:
            await session.rollback()
            raise OutboxDeliveryError(
                "dispatch_configuration",
                retryable=False,
            )
        if call.livekit_dispatch_id not in (None, dispatch_id):
            await session.rollback()
            raise OutboxDeliveryError(
                "dispatch_conflict",
                retryable=False,
            )
        await CallRepository(session).set_livekit_dispatch_id(
            call,
            livekit_dispatch_id=dispatch_id,
        )
        await session.commit()


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
        except ValueError:
            raise OutboxDeliveryError(
                "dispatch_configuration",
                retryable=False,
            ) from None
        except Exception:
            raise OutboxDeliveryError(
                "provider_retryable",
                retryable=True,
            ) from None
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
            except ValueError:
                raise OutboxDeliveryError(
                    "dispatch_configuration",
                    retryable=False,
                ) from None
            except Exception:
                try:
                    dispatches = await provider.list_dispatches(
                        room_name=snapshot.room_name
                    )
                except Exception:
                    raise OutboxDeliveryError(
                        "provider_retryable",
                        retryable=True,
                    ) from None
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

        try:
            worker_name = get_settings().livekit_agent_name.strip()
            if not worker_name:
                raise ValueError
            metadata = VerificationDispatchMetadata(
                verification_session_id=session_id,
                user_id=str(user_id),
                agent_identity=f"agent-verification-{session_id}",
                completion_token=create_verification_token(
                    session_id=session_id,
                    user_id=str(user_id),
                ),
            ).model_dump_json()
        except Exception:
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


def _validated_lifecycle_generation(event: OutboxEvent) -> int:
    lifecycle_generation = event.payload.get("lifecycle_generation")
    if type(lifecycle_generation) is not int or lifecycle_generation < 1:
        raise OutboxDeliveryError(
            "dispatch_configuration",
            retryable=False,
        )
    return lifecycle_generation


async def _require_current_worker_account(
    session_factory,
    user_id: UUID,
    *,
    lifecycle_generation: int,
) -> None:
    async with session_factory() as session:
        user = await UserRepository(session).get_by_id_for_update(user_id)
        if user is None:
            await session.rollback()
            raise OutboxDeliveryError(
                "dispatch_ineligible",
                retryable=False,
            )
        try:
            require_current_account_lifecycle(
                user,
                lifecycle_generation=lifecycle_generation,
            )
        except (AccountStateBlockedError, AccountLifecycleGenerationMismatchError):
            await session.rollback()
            raise OutboxDeliveryError(
                "dispatch_ineligible",
                retryable=False,
            ) from None
        await session.commit()


def _reconcile_verification_dispatches(
    snapshot: _VerificationDispatchSnapshot,
    dispatches: list[LiveKitDispatch],
) -> LiveKitDispatch | None:
    named_dispatches = [
        dispatch for dispatch in dispatches if dispatch.agent_name.strip()
    ]
    matches: list[LiveKitDispatch] = []
    for dispatch in named_dispatches:
        try:
            metadata = json.loads(dispatch.metadata)
        except (TypeError, ValueError):
            metadata = None
        if (
            dispatch.agent_name == snapshot.worker_name
            and dispatch.room == snapshot.room_name
            and isinstance(metadata, dict)
            and metadata.get("job_type") == "forwarding_verification"
            and metadata.get("verification_session_id") == snapshot.session_id
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
        except Exception:
            raise OutboxDeliveryError("provider_retryable", retryable=True) from None
        if summary_data is None:
            raise OutboxDeliveryError("provider_retryable", retryable=True)

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
    except Exception:
        observability.record_recording_reconciliation_result("recording_unresolved")
        raise OutboxDeliveryError(
            "recording_unresolved",
            retryable=True,
            exhaustible=False,
        ) from None

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
