from dataclasses import dataclass
from typing import Any
from uuid import UUID

from app.core.database import get_session_factory
from app.models.outbox_event import OutboxEvent
from app.models.phone_number import PhoneNumber
from app.repositories.agent_config_repository import AgentConfigRepository
from app.repositories.phone_number_repository import PhoneNumberRepository
from app.repositories.subscription_repository import SubscriptionRepository
from app.repositories.usage_repository import UsageRepository
from app.services.onboarding_service import OnboardingService
from app.services.subscription_access_policy import SubscriptionAccessPolicy
from app.workers.jobs.outbox_delivery import OutboxDeliveryError
from app.workers.jobs.phone_provisioning import phone_provisioning_job


@dataclass(frozen=True)
class _RoutingSnapshot:
    phone_number_id: UUID
    provider_number_id: str
    should_enable: bool
    is_active: bool
    provider_connection_name: str | None


async def deliver_phone_provision(
    ctx: dict[str, Any],
    event: OutboxEvent,
) -> None:
    await phone_provisioning_job(
        ctx,
        dict(event.payload),
        operation_key=event.idempotency_key,
    )
    await deliver_phone_routing(ctx, event)


async def deliver_phone_routing(
    ctx: dict[str, Any],
    event: OutboxEvent,
) -> None:
    user_id = UUID(event.payload["user_id"])
    session_factory = ctx.get("session_factory") or get_session_factory()
    async with session_factory() as session:
        snapshot = await _routing_snapshot(session, user_id)
        await session.commit()

    if snapshot is None:
        return
    desired_connection_name = "app-active" if snapshot.should_enable else "app-disabled"

    provider = ctx.get("telephony_provider")
    if provider is None:
        from app.providers.telephony.telnyx import TelephonyTelnyx

        provider = TelephonyTelnyx()
    if snapshot.should_enable:
        provider_connection_name = await provider.enable_number(
            provider_number_id=snapshot.provider_number_id
        )
    else:
        provider_connection_name = await provider.disable_number(
            provider_number_id=snapshot.provider_number_id
        )
    if provider_connection_name != desired_connection_name:
        raise OutboxDeliveryError("provider_retryable", retryable=True)

    async with session_factory() as session:
        current = await _routing_snapshot(session, user_id)
        if current is None:
            await session.rollback()
            return
        if current.should_enable != snapshot.should_enable:
            await session.rollback()
            raise OutboxDeliveryError("provider_retryable", retryable=True)
        phone_number = await session.get(
            PhoneNumber,
            snapshot.phone_number_id,
            with_for_update=True,
        )
        if phone_number is None:
            await session.rollback()
            return
        phone_number.provider_connection_name = provider_connection_name
        phone_number.is_active = provider_connection_name == "app-active"
        await session.commit()


async def _routing_snapshot(session, user_id: UUID) -> _RoutingSnapshot | None:
    phone_number = await PhoneNumberRepository(session).get_by_user_id(user_id)
    if phone_number is None:
        return None
    if not phone_number.provider_number_id:
        raise OutboxDeliveryError("provider_terminal", retryable=False)
    subscription = await SubscriptionRepository(session).get_by_user_id(user_id)
    agent_config = await AgentConfigRepository(session).get_by_user_id(user_id)
    balance = await UsageRepository(session).get_current_balance(user_id=user_id)
    should_enable = bool(
        subscription is not None
        and SubscriptionAccessPolicy.can_route(
            subscription.status,
            subscription.current_period_end,
        )
        and balance > 0
        and agent_config is not None
        and agent_config.is_enabled
        and OnboardingService._is_agent_setup_complete(agent_config)
    )
    return _RoutingSnapshot(
        phone_number_id=phone_number.id,
        provider_number_id=phone_number.provider_number_id,
        should_enable=should_enable,
        is_active=phone_number.is_active,
        provider_connection_name=phone_number.provider_connection_name,
    )


async def deliver_future_topic(
    _ctx: dict[str, Any],
    _event: OutboxEvent,
) -> None:
    # Task 8 activates LiveKit dispatch. Task 10 activates recording and
    # notification producers once their provider replay contracts are complete.
    raise OutboxDeliveryError("handler_configuration", retryable=False)


DEFAULT_OUTBOX_HANDLERS = {
    "phone.provision": deliver_phone_provision,
    "phone.enable": deliver_phone_routing,
    "phone.disable": deliver_phone_routing,
    "livekit.dispatch": deliver_future_topic,
    "recording.start": deliver_future_topic,
    "notification.send": deliver_future_topic,
}
