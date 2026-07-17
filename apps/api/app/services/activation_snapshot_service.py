from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.business_profile import BusinessProfile
from app.models.customer_activation import CustomerActivation
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
from app.schemas.activation import (
    ActivationBillingResponse,
    ActivationNumberResponse,
    ActivationProgressResponse,
    ActivationSnapshotResponse,
    RuntimeReadinessResponse,
)
from app.schemas.business_profile import (
    BusinessProfileConstraints,
    BusinessProfileResponse,
)
from app.services.activation_policy import ActivationFacts, ActivationPolicy
from app.services.customer_readiness_service import (
    activation_readiness_prerequisites,
    build_customer_readiness_snapshot,
)
from app.services.customer_readiness_policy import CustomerReadinessPolicy


class ActivationSnapshotUnavailableError(Exception):
    pass


class ActivationSnapshotService:
    def __init__(
        self,
        session: AsyncSession | None = None,
        *,
        user_repository: UserRepository | None = None,
        business_profile_repository: BusinessProfileRepository | None = None,
        activation_repository: CustomerActivationRepository | None = None,
        subscription_repository: SubscriptionRepository | None = None,
        usage_repository: UsageRepository | None = None,
        provisioning_repository: PhoneNumberProvisioningRepository | None = None,
        phone_number_repository: PhoneNumberRepository | None = None,
        agent_config_repository: AgentConfigRepository | None = None,
    ) -> None:
        required_session = self._require_session
        self.user_repository = user_repository or UserRepository(
            required_session(session)
        )
        self.business_profile_repository = (
            business_profile_repository
            or BusinessProfileRepository(required_session(session))
        )
        self.activation_repository = activation_repository or CustomerActivationRepository(
            required_session(session)
        )
        self.subscription_repository = subscription_repository or SubscriptionRepository(
            required_session(session)
        )
        self.usage_repository = usage_repository or UsageRepository(
            required_session(session)
        )
        self.provisioning_repository = (
            provisioning_repository
            or PhoneNumberProvisioningRepository(required_session(session))
        )
        self.phone_number_repository = phone_number_repository or PhoneNumberRepository(
            required_session(session)
        )
        self.agent_config_repository = agent_config_repository or AgentConfigRepository(
            required_session(session)
        )

    async def get(
        self,
        user_id: UUID,
        now: datetime | None = None,
    ) -> ActivationSnapshotResponse:
        evaluation_time = now or datetime.now(UTC)
        user = await self.user_repository.get_by_id(user_id)
        if user is None:
            raise ActivationSnapshotUnavailableError
        profile = await self.business_profile_repository.get_by_user_id(user_id)
        activation = await self.activation_repository.get_by_user_id(user_id)
        subscription = await self.subscription_repository.get_by_user_id(user_id)
        balance = await self.usage_repository.get_current_balance(user_id=user_id)
        provisioning = await self.provisioning_repository.get_by_user_id(user_id)
        phone = await self.phone_number_repository.get_by_user_id(user_id)
        agent_config = await self.agent_config_repository.get_by_user_id(user_id)

        activation_prerequisites = activation_readiness_prerequisites(
            profile=profile,
            activation=activation,
            phone_number=phone,
            agent_config=agent_config,
        )
        readiness = CustomerReadinessPolicy.evaluate(
            build_customer_readiness_snapshot(
                user=user,
                subscription=subscription,
                balance=balance,
                phone_number=phone,
                provisioning=provisioning,
                agent_config=agent_config,
                activation_required=True,
                business_profile_complete=(
                    activation_prerequisites.business_profile_complete
                ),
                profile_projection_current=(
                    activation_prerequisites.profile_projection_current
                ),
                forwarding_verified=activation_prerequisites.forwarding_verified,
                go_live_approved=activation_prerequisites.go_live_approved,
            ),
            now=evaluation_time,
        )
        evaluated_at = readiness.evaluated_at
        billing_eligible = readiness.subscription_eligible
        facts = ActivationFacts(
            profile_confirmed=bool(
                profile is not None
                and activation_prerequisites.business_profile_complete
                and activation is not None
                and activation.profile_confirmed_at is not None
            ),
            subscription_eligible=billing_eligible,
            provisioning_consented=bool(
                activation is not None
                and activation.provisioning_consented_at is not None
            ),
            provisioning_status=(
                provisioning.status if provisioning is not None else None
            ),
            phone_ready=bool(phone is not None and phone.provider_number_id),
            verification_window_open=self._window_is_open(
                activation,
                evaluated_at,
            ),
            forwarding_verified=activation_prerequisites.forwarding_verified,
            go_live_pending=bool(
                activation_prerequisites.go_live_approved
                and activation is not None
                and activation.activated_at is None
            ),
            go_live_approved=activation_prerequisites.go_live_approved,
            runtime_ready=readiness.can_route,
            runtime_blockers=tuple(str(blocker) for blocker in readiness.blockers),
        )
        decision = ActivationPolicy.evaluate(facts)

        return ActivationSnapshotResponse(
            workflow_version=activation.workflow_version if activation is not None else 1,
            stage=decision.stage,
            completed_milestones=list(decision.completed_milestones),
            next_action=decision.next_action,
            blockers=list(decision.blockers),
            warnings=list(readiness.warnings),
            profile=self._profile_response(profile),
            profile_constraints=BusinessProfileConstraints(),
            activation=self._activation_response(activation),
            billing=ActivationBillingResponse(
                eligible=billing_eligible,
                plan_tier=subscription.plan_tier if subscription is not None else None,
                subscription_status=(
                    subscription.status if subscription is not None else None
                ),
                allocated_minutes=(
                    subscription.allocated_minutes if subscription is not None else 0
                ),
                minutes_remaining=balance,
                current_period_start=(
                    subscription.current_period_start
                    if subscription is not None
                    else None
                ),
                current_period_end=(
                    subscription.current_period_end
                    if subscription is not None
                    else None
                ),
            ),
            number=ActivationNumberResponse(
                assigned_e164=phone.e164 if phone is not None else None,
                country_code=phone.country_code if phone is not None else None,
                provider_ready=bool(phone is not None and phone.provider_number_id),
                provisioning_status=(
                    provisioning.status if provisioning is not None else None
                ),
                can_retry=bool(provisioning is not None and provisioning.can_retry),
            ),
            runtime_readiness=RuntimeReadinessResponse(
                stage=readiness.stage,
                can_provision_number=readiness.can_provision_number,
                can_activate=readiness.can_activate,
                should_enable_phone=readiness.should_enable_phone,
                can_route=readiness.can_route,
                blockers=[str(blocker) for blocker in readiness.blockers],
                warnings=list(readiness.warnings),
                policy_version=readiness.policy_version,
            ),
            evaluated_at=evaluated_at,
        )

    @staticmethod
    def _profile_response(profile: BusinessProfile | None) -> BusinessProfileResponse:
        if profile is None:
            return BusinessProfileResponse(content_revision=1, routing_revision=1)
        return BusinessProfileResponse.model_validate(profile)

    @staticmethod
    def _activation_response(
        activation: CustomerActivation | None,
    ) -> ActivationProgressResponse:
        return ActivationProgressResponse(
            profile_confirmed_at=(
                activation.profile_confirmed_at if activation is not None else None
            ),
            provisioning_consented_at=(
                activation.provisioning_consented_at
                if activation is not None
                else None
            ),
            forwarding_verified_at=(
                activation.forwarding_verified_at if activation is not None else None
            ),
            go_live_approved_at=(
                activation.go_live_approved_at if activation is not None else None
            ),
            activated_at=activation.activated_at if activation is not None else None,
            last_failure_code=(
                activation.last_failure_code if activation is not None else None
            ),
        )

    @staticmethod
    def _window_is_open(
        activation: CustomerActivation | None,
        now: datetime,
    ) -> bool:
        if (
            activation is None
            or activation.verification_status not in {"open", "claimed"}
            or activation.verification_window_started_at is None
            or activation.verification_window_expires_at is None
        ):
            return False
        return (
            CustomerReadinessPolicy._as_utc(
                activation.verification_window_started_at
            )
            <= now
            < CustomerReadinessPolicy._as_utc(
                activation.verification_window_expires_at
            )
        )

    @staticmethod
    def _require_session(session: AsyncSession | None) -> AsyncSession:
        if session is None:
            raise ValueError("session is required when repositories are not provided")
        return session
