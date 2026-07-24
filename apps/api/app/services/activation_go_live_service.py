from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.customer_activation import CustomerActivation
from app.models.outbox_event import OutboxEvent
from app.repositories.activation_event_repository import ActivationEventRepository
from app.repositories.agent_config_repository import AgentConfigRepository
from app.repositories.business_profile_repository import BusinessProfileRepository
from app.repositories.customer_activation_repository import (
    CustomerActivationRepository,
)
from app.repositories.phone_number_provisioning_repository import (
    PhoneNumberProvisioningRepository,
)
from app.repositories.phone_number_repository import PhoneNumberRepository
from app.repositories.subscription_repository import SubscriptionRepository
from app.repositories.usage_repository import UsageRepository
from app.repositories.user_repository import UserRepository
from app.schemas.activation import ActivationSnapshotResponse
from app.services.activation_snapshot_service import ActivationSnapshotService
from app.services.account_access_policy import require_active_account
from app.services.customer_readiness_policy import ReadinessBlocker
from app.services.customer_readiness_service import evaluate_customer_readiness
from app.services.forwarding_verification_service import as_utc
from app.services.outbox_service import OutboxService


logger = logging.getLogger(__name__)

_GO_LIVE_PROJECTION_BLOCKERS = frozenset(
    {
        ReadinessBlocker.AGENT_DISABLED,
        ReadinessBlocker.PHONE_INACTIVE,
        ReadinessBlocker.PHONE_PROJECTION_INACTIVE,
        ReadinessBlocker.GO_LIVE_NOT_APPROVED,
        ReadinessBlocker.GO_LIVE_NOT_ACTIVATED,
    }
)
_TERMINAL_FAILURE_CODE = "routing_provider_terminal"
_UNIX_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

# Every activation-aware transaction uses this lock order: user, customer
# activation, business profile, phone/provisioning, subscription, then agent
# configuration. Usage is read only after the User serialization boundary.


class ActivationGoLiveBlockedError(Exception):
    def __init__(self, blockers: tuple[str, ...]) -> None:
        super().__init__("Go-live prerequisites are not satisfied")
        self.blockers = blockers


def go_live_attempt_token(requested_at: datetime) -> str:
    delta = as_utc(requested_at) - _UNIX_EPOCH
    total_seconds = delta.days * 86_400 + delta.seconds
    return str(total_seconds * 1_000_000 + delta.microseconds)


def go_live_outbox_key(activation_id: UUID, requested_at: datetime) -> str:
    return f"activation:go-live:{activation_id}:attempt:{go_live_attempt_token(requested_at)}"


def is_current_go_live_event(
    event: OutboxEvent,
    activation: CustomerActivation | None,
) -> bool:
    return bool(
        activation is not None
        and activation.go_live_requested_at is not None
        and event.topic == "phone.enable"
        and event.aggregate_type == "user"
        and event.aggregate_id == activation.user_id
        and event.idempotency_key
        == go_live_outbox_key(activation.id, activation.go_live_requested_at)
    )


def is_go_live_event(event: OutboxEvent) -> bool:
    return bool(
        event.topic == "phone.enable"
        and event.idempotency_key.startswith("activation:go-live:")
    )


class ActivationGoLiveService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        now_provider: Callable[[], datetime] | None = None,
        snapshot_service: ActivationSnapshotService | None = None,
    ) -> None:
        self.session = session
        self.user_repository = UserRepository(session)
        self.activation_repository = CustomerActivationRepository(session)
        self.business_profile_repository = BusinessProfileRepository(session)
        self.phone_number_repository = PhoneNumberRepository(session)
        self.provisioning_repository = PhoneNumberProvisioningRepository(session)
        self.subscription_repository = SubscriptionRepository(session)
        self.agent_config_repository = AgentConfigRepository(session)
        self.usage_repository = UsageRepository(session)
        self.activation_event_repository = ActivationEventRepository(session)
        self.outbox_service = OutboxService(session)
        self.snapshot_service = snapshot_service or ActivationSnapshotService(session)
        self.now_provider = now_provider or (lambda: datetime.now(UTC))

    async def go_live(
        self,
        user_id: UUID,
        arq_pool,
    ) -> ActivationSnapshotResponse:
        should_wake = False
        try:
            user = await self.user_repository.get_by_id_for_update(user_id)
            if user is None:
                raise ActivationGoLiveBlockedError(("business_profile_incomplete",))
            require_active_account(user)
            activation = await self.activation_repository.get_by_user_id_for_update(
                user_id
            )
            profile = await self.business_profile_repository.get_by_user_id_for_update(
                user_id
            )
            phone = await self.phone_number_repository.get_by_user_id_for_update(
                user_id
            )
            provisioning = (
                await self.provisioning_repository.get_by_user_id_for_update(user_id)
            )
            subscription = (
                await self.subscription_repository.get_by_user_id_for_update(user_id)
            )
            config = await self.agent_config_repository.get_by_user_id_for_update(
                user_id
            )
            balance = await self.usage_repository.get_current_balance(user_id=user_id)

            if activation is None:
                raise ActivationGoLiveBlockedError(("business_profile_incomplete",))

            actual_readiness = evaluate_customer_readiness(
                user=user,
                subscription=subscription,
                balance=balance,
                phone_number=phone,
                provisioning=provisioning,
                agent_config=config,
                business_profile=profile,
                activation=activation,
                activation_required=True,
                now=self.now_provider(),
            )

            if activation.activated_at is not None and actual_readiness.can_route:
                await self.session.commit()
                return await self.snapshot_service.get(user_id)
            if (
                activation.go_live_requested_at is not None
                and activation.go_live_approved_at is not None
                and activation.activated_at is None
            ):
                await self.session.commit()
                return await self.snapshot_service.get(user_id)

            readiness = evaluate_customer_readiness(
                user=user,
                subscription=subscription,
                balance=balance,
                phone_number=phone,
                provisioning=provisioning,
                agent_config=config,
                business_profile=profile,
                activation=activation,
                activation_required=True,
                agent_enabled_override=True,
                go_live_approved_override=True,
                now=self.now_provider(),
            )

            blockers = tuple(
                blocker.value
                for blocker in readiness.blockers
                if blocker not in _GO_LIVE_PROJECTION_BLOCKERS
            )
            if blockers:
                raise ActivationGoLiveBlockedError(blockers)
            if user is None or profile is None or phone is None or config is None:
                raise RuntimeError("readiness approved go-live without required state")

            attempt_at = await self._next_attempt_at(activation)
            activation.go_live_requested_at = attempt_at
            activation.go_live_approved_at = attempt_at
            activation.activated_at = None
            activation.last_failure_code = None
            config.is_enabled = True

            outbox_key = go_live_outbox_key(activation.id, attempt_at)
            await self.outbox_service.add(
                topic="phone.enable",
                aggregate_type="user",
                aggregate_id=user_id,
                idempotency_key=outbox_key,
                payload={
                    "user_id": str(user_id),
                    "lifecycle_generation": user.lifecycle_generation,
                },
            )
            await self.activation_event_repository.append(
                user_id=user_id,
                activation_id=activation.id,
                event_type="go_live_requested",
                idempotency_key=f"activation-event:{outbox_key}:requested",
                metadata={"attempt": go_live_attempt_token(attempt_at)},
            )
            await self.session.commit()
            should_wake = True
        except Exception:
            await self.session.rollback()
            raise

        if should_wake:
            await self._wake_outbox(arq_pool)
        return await self.snapshot_service.get(user_id)

    async def _next_attempt_at(self, activation: CustomerActivation) -> datetime:
        candidate = as_utc(self.now_provider())
        candidate_token = int(go_live_attempt_token(candidate))
        latest_token = await self.activation_event_repository.get_latest_attempt_token(
            user_id=activation.user_id,
            event_type="go_live_requested",
        )
        if latest_token is not None and candidate_token <= latest_token:
            candidate_token = latest_token + 1
        seconds, microseconds = divmod(candidate_token, 1_000_000)
        return datetime.fromtimestamp(seconds, tz=UTC) + timedelta(
            microseconds=microseconds
        )

    @staticmethod
    async def _wake_outbox(arq_pool) -> None:
        if arq_pool is None:
            return
        try:
            await arq_pool.enqueue_job("outbox_delivery_job", {})
        except Exception as error:
            logger.warning(
                "outbox wakeup enqueue failed operation=activation_go_live "
                "error_type=%s",
                type(error).__name__,
            )


async def mark_current_go_live_succeeded(
    session: AsyncSession,
    *,
    event: OutboxEvent,
    activation: CustomerActivation,
    succeeded_at: datetime,
) -> bool:
    if activation.activated_at is not None or not is_current_go_live_event(
        event, activation
    ):
        return False
    attempt_at = activation.go_live_requested_at
    if attempt_at is None:
        return False
    activation.activated_at = as_utc(succeeded_at)
    await ActivationEventRepository(session).append(
        user_id=activation.user_id,
        activation_id=activation.id,
        event_type="go_live_succeeded",
        idempotency_key=(
            f"activation-event:{go_live_outbox_key(activation.id, attempt_at)}:succeeded"
        ),
        metadata={"attempt": go_live_attempt_token(attempt_at)},
    )
    return True


async def fail_current_go_live_attempt(
    session: AsyncSession,
    *,
    event: OutboxEvent,
) -> bool:
    if not is_go_live_event(event) or event.aggregate_type != "user":
        return False
    user_id = event.aggregate_id
    user = await UserRepository(session).get_by_id_for_update(user_id)
    if user is None:
        return False
    activation = await CustomerActivationRepository(
        session
    ).get_by_user_id_for_update(user_id)
    await BusinessProfileRepository(session).get_by_user_id_for_update(user_id)
    phone = await PhoneNumberRepository(session).get_by_user_id_for_update(user_id)
    await PhoneNumberProvisioningRepository(session).get_by_user_id_for_update(user_id)
    await SubscriptionRepository(session).get_by_user_id_for_update(user_id)
    config = await AgentConfigRepository(session).get_by_user_id_for_update(user_id)
    if (
        activation is None
        or activation.activated_at is not None
        or activation.go_live_approved_at is None
        or not is_current_go_live_event(event, activation)
    ):
        return False

    attempt_at = activation.go_live_requested_at
    if attempt_at is None:
        return False
    if phone is not None:
        phone.is_active = False
        phone.provider_connection_name = "app-disabled"
    if config is not None:
        config.is_enabled = False
    activation.go_live_requested_at = None
    activation.go_live_approved_at = None
    activation.activated_at = None
    activation.last_failure_code = _TERMINAL_FAILURE_CODE
    await ActivationEventRepository(session).append(
        user_id=user_id,
        activation_id=activation.id,
        event_type="go_live_failed",
        idempotency_key=(
            f"activation-event:{go_live_outbox_key(activation.id, attempt_at)}:failed"
        ),
        metadata={
            "attempt": go_live_attempt_token(attempt_at),
            "failure_code": _TERMINAL_FAILURE_CODE,
        },
    )
    return True
