from __future__ import annotations

from typing import Literal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.agent_config_repository import AgentConfigRepository
from app.repositories.phone_number_provisioning_repository import (
    PhoneNumberProvisioningRepository,
)
from app.repositories.phone_number_repository import PhoneNumberRepository
from app.repositories.subscription_repository import SubscriptionRepository
from app.repositories.usage_repository import UsageRepository
from app.repositories.user_repository import UserRepository
from app.schemas.activation import ActivationSnapshotResponse
from app.schemas.onboarding import OnboardingStatusResponse
from app.services.activation_provisioning_service import (
    ActivationProvisioningBlockedError,
    ActivationProvisioningService,
)
from app.services.customer_readiness_policy import (
    CustomerReadinessStage,
    ReadinessBlocker,
)
from app.services.customer_readiness_service import CustomerReadinessService


OnboardingRetryNotAllowedError = ActivationProvisioningBlockedError


class OnboardingService:
    _AGENT_SETUP_BLOCKERS = frozenset(
        {
            ReadinessBlocker.AGENT_CONFIG_MISSING,
            ReadinessBlocker.AGENT_SETUP_INCOMPLETE,
            ReadinessBlocker.AGENT_CONTENT_INVALID,
        }
    )

    def __init__(
        self,
        session: AsyncSession | None = None,
        *,
        activation_flow_enabled: bool,
        readiness_service: CustomerReadinessService | None = None,
        user_repository: UserRepository | None = None,
        subscription_repository: SubscriptionRepository | None = None,
        usage_repository: UsageRepository | None = None,
        phone_number_repository: PhoneNumberRepository | None = None,
        provisioning_repository: PhoneNumberProvisioningRepository | None = None,
        agent_config_repository: AgentConfigRepository | None = None,
        activation_provisioning_service: ActivationProvisioningService | None = None,
    ) -> None:
        if readiness_service is None:
            readiness_service = CustomerReadinessService(
                session,
                activation_flow_enabled=activation_flow_enabled,
                user_repository=user_repository,
                subscription_repository=subscription_repository,
                usage_repository=usage_repository,
                phone_number_repository=phone_number_repository,
                provisioning_repository=provisioning_repository,
                agent_config_repository=agent_config_repository,
            )

        self.readiness_service = readiness_service
        self.provisioning_repository = (
            provisioning_repository or readiness_service.provisioning_repository
        )
        self.session = session
        self.activation_provisioning_service = activation_provisioning_service
        if self.activation_provisioning_service is None and session is not None:
            self.activation_provisioning_service = ActivationProvisioningService(
                session
            )

    async def get_status(self, user_id: UUID | str) -> OnboardingStatusResponse:
        context = await self.readiness_service.evaluate(user_id)
        result = context.result
        phone_number_status = self._phone_number_status(
            phone_usable=bool(
                context.phone_number is not None
                and context.phone_number.provider_number_id
            ),
            provisioning_status=(
                context.provisioning.status
                if context.provisioning is not None
                else None
            ),
            readiness_stage=result.stage,
        )
        agent_setup_complete = not bool(
            set(result.blockers) & self._AGENT_SETUP_BLOCKERS
        )
        can_retry_provisioning = bool(
            context.provisioning is not None
            and context.provisioning.status == "failed"
            and context.provisioning.can_retry
            and context.phone_number is None
            and result.can_provision_number
        )

        return OnboardingStatusResponse(
            subscription_status=(
                context.subscription.status
                if context.subscription is not None
                else None
            ),
            plan_tier=(
                context.subscription.plan_tier
                if context.subscription is not None
                else None
            ),
            minutes_remaining=context.balance,
            phone_number=(
                context.phone_number.e164 if context.phone_number is not None else None
            ),
            phone_number_status=phone_number_status,
            agent_setup_complete=agent_setup_complete,
            can_retry_provisioning=can_retry_provisioning,
            stage=result.stage.value,
            can_activate=result.can_activate,
            can_route=result.can_route,
            blockers=[blocker.value for blocker in result.blockers],
            warnings=list(result.warnings),
            evaluated_at=result.evaluated_at,
            policy_version=result.policy_version,
        )

    async def retry_provisioning(
        self,
        user_id: UUID | str,
        *,
        arq_pool,
    ) -> ActivationSnapshotResponse:
        if self.activation_provisioning_service is None:
            raise RuntimeError("A database session is required for provisioning retry")
        return await self.activation_provisioning_service.retry(
            UUID(str(user_id)),
            arq_pool=arq_pool,
        )

    @staticmethod
    def _phone_number_status(
        *,
        phone_usable: bool,
        provisioning_status: str | None,
        readiness_stage: CustomerReadinessStage,
    ) -> Literal["missing", "provisioning", "ready", "failed"]:
        if (
            provisioning_status == "failed"
            or readiness_stage == CustomerReadinessStage.NUMBER_PROVISIONING_FAILED
        ):
            return "failed"
        if provisioning_status in {"queued", "running"}:
            return "provisioning"
        if phone_usable:
            return "ready"
        return "missing"
