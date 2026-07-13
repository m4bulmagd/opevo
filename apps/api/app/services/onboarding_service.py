from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.agent_config_repository import AgentConfigRepository
from app.repositories.phone_number_provisioning_repository import PhoneNumberProvisioningRepository
from app.repositories.phone_number_repository import PhoneNumberRepository
from app.repositories.subscription_repository import SubscriptionRepository
from app.repositories.usage_repository import UsageRepository
from app.schemas.onboarding import OnboardingStatusResponse, RetryProvisioningResponse
from app.services.outbox_service import OutboxService
from app.services.subscription_access_policy import SubscriptionAccessPolicy


class OnboardingRetryNotAllowedError(Exception):
    pass


logger = logging.getLogger(__name__)


class OnboardingService:
    def __init__(
        self,
        session: AsyncSession | None = None,
        *,
        subscription_repository: SubscriptionRepository | None = None,
        usage_repository: UsageRepository | None = None,
        phone_number_repository: PhoneNumberRepository | None = None,
        provisioning_repository: PhoneNumberProvisioningRepository | None = None,
        agent_config_repository: AgentConfigRepository | None = None,
    ) -> None:
        if (
            subscription_repository is None
            or usage_repository is None
            or phone_number_repository is None
            or provisioning_repository is None
            or agent_config_repository is None
        ):
            if session is None:
                raise ValueError("session is required when repositories are not provided")
            subscription_repository = subscription_repository or SubscriptionRepository(session)
            usage_repository = usage_repository or UsageRepository(session)
            phone_number_repository = phone_number_repository or PhoneNumberRepository(session)
            provisioning_repository = provisioning_repository or PhoneNumberProvisioningRepository(session)
            agent_config_repository = agent_config_repository or AgentConfigRepository(session)

        self.subscription_repository = subscription_repository
        self.usage_repository = usage_repository
        self.phone_number_repository = phone_number_repository
        self.provisioning_repository = provisioning_repository
        self.agent_config_repository = agent_config_repository
        self.session = session
        self.outbox_service = OutboxService(session) if session is not None else None

    async def get_status(self, user_id: UUID | str) -> OnboardingStatusResponse:
        subscription = await self.subscription_repository.get_by_user_id(user_id)
        minutes_remaining = await self.usage_repository.get_current_balance(
            user_id=user_id
        )
        phone_number = await self.phone_number_repository.get_by_user_id(user_id)
        provisioning = await self.provisioning_repository.get_by_user_id(user_id)
        config = await self.agent_config_repository.get_by_user_id(user_id)

        subscription_status = subscription.status if subscription is not None else None
        plan_tier = subscription.plan_tier if subscription is not None else None
        subscription_access = bool(
            subscription is not None
            and subscription.plan_tier == "starter"
            and SubscriptionAccessPolicy.can_route(
                subscription.status,
                getattr(subscription, "current_period_end", None),
            )
        )

        if provisioning is not None and provisioning.status == "failed":
            phone_number_status = "failed"
        elif provisioning is not None and provisioning.status in {"queued", "running"}:
            phone_number_status = "provisioning"
        elif phone_number is not None or (provisioning is not None and provisioning.status == "succeeded"):
            phone_number_status = "ready"
        else:
            phone_number_status = "missing"

        agent_setup_complete = self._is_agent_setup_complete(config)
        routing_enabled = bool(
            subscription_access
            and agent_setup_complete
            and config is not None
            and config.is_enabled
            and phone_number is not None
            and phone_number.is_active
        )
        can_retry_provisioning = bool(
            provisioning is not None
            and provisioning.status == "failed"
            and provisioning.can_retry
            and phone_number is None
            and subscription_access
        )

        overall_status = self._derive_overall_status(
            subscription_active=subscription_access,
            phone_number_status=phone_number_status,
            agent_setup_complete=agent_setup_complete,
            routing_enabled=routing_enabled,
        )

        return OnboardingStatusResponse(
            subscription_status=subscription_status,
            plan_tier=plan_tier,
            minutes_remaining=minutes_remaining,
            phone_number=phone_number.e164 if phone_number is not None else None,
            phone_number_status=phone_number_status,
            routing_enabled=routing_enabled,
            agent_setup_complete=agent_setup_complete,
            overall_status=overall_status,
            can_retry_provisioning=can_retry_provisioning,
        )

    async def retry_provisioning(self, user_id: UUID | str, *, arq_pool) -> RetryProvisioningResponse:
        if self.session is None or self.outbox_service is None:
            raise RuntimeError("A database session is required for provisioning retry")
        user_uuid = UUID(str(user_id))
        provisioning = await self.provisioning_repository.get_by_user_id_for_update(
            user_uuid
        )
        if (
            provisioning is None
            or provisioning.status != "failed"
            or not provisioning.can_retry
        ):
            raise OnboardingRetryNotAllowedError

        subscription = await self.subscription_repository.get_by_user_id(user_uuid)
        phone_number = await self.phone_number_repository.get_by_user_id(user_uuid)
        if (
            subscription is None
            or not SubscriptionAccessPolicy.can_route(
                subscription.status,
                subscription.current_period_end,
            )
            or phone_number is not None
        ):
            raise OnboardingRetryNotAllowedError

        next_attempt = provisioning.attempt_count + 1
        provisioning.status = "queued"
        provisioning.can_retry = False
        await self.outbox_service.add(
            topic="phone.provision",
            aggregate_type="user",
            aggregate_id=user_uuid,
            idempotency_key=(
                f"onboarding:phone.provision:{provisioning.id}:attempt:{next_attempt}"
            ),
            payload={"user_id": str(user_uuid)},
        )
        await self.session.commit()

        if arq_pool is not None:
            try:
                await arq_pool.enqueue_job("outbox_delivery_job", {})
            except Exception as error:
                logger.warning(
                    "outbox wakeup enqueue failed operation=retry_phone_provisioning "
                    "error_type=%s",
                    type(error).__name__,
                )
        return RetryProvisioningResponse(status="accepted", queued=True)

    @staticmethod
    def _is_agent_setup_complete(config) -> bool:
        if config is None:
            return False

        agent_name = (config.agent_name or "").strip()
        owner_context = (config.owner_context or "").strip()
        system_prompt = (config.system_prompt or "").strip()
        knowledge_base = (config.knowledge_base or "").strip()

        return (
            bool(agent_name)
            and agent_name != "Assistant"
            and bool(owner_context)
            and bool(system_prompt or knowledge_base)
        )

    @staticmethod
    def _derive_overall_status(
        *,
        subscription_active: bool,
        phone_number_status: str,
        agent_setup_complete: bool,
        routing_enabled: bool,
    ) -> str:
        if routing_enabled:
            return "live"
        if phone_number_status == "failed":
            return "provisioning_failed"
        if subscription_active and phone_number_status == "provisioning":
            return "provisioning_number"
        if subscription_active and phone_number_status == "ready" and not agent_setup_complete:
            return "setup_required"
        if subscription_active and phone_number_status == "ready" and agent_setup_complete:
            return "ready_to_enable"
        if subscription_active:
            return "subscription_active"
        return "not_subscribed"
