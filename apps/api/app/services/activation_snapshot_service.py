from datetime import UTC, datetime
from typing import cast
from uuid import UUID

import phonenumbers

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.business_profile import BusinessProfile
from app.models.customer_activation import CustomerActivation
from app.models.phone_number import PhoneNumber
from app.providers.carrier_lookup.base import CarrierCode
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
from app.schemas.forwarding import ForwardingGuide
from app.services.activation_policy import ActivationFacts, ActivationPolicy
from app.services.customer_readiness_service import (
    activation_readiness_prerequisites,
    build_customer_readiness_snapshot,
)
from app.services.customer_readiness_policy import CustomerReadinessPolicy
from app.services.forwarding_instruction_catalog import ForwardingInstructionCatalog
from app.services.forwarding_verification_service import COMPLETION_GRACE, as_utc


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
                go_live_activated=activation_prerequisites.go_live_activated,
            ),
            now=evaluation_time,
        )
        evaluated_at = readiness.evaluated_at
        billing_eligible = readiness.subscription_eligible
        number_provisioned = bool(
            provisioning is not None
            and provisioning.status == "succeeded"
            and phone is not None
            and provisioning.phone_number_id == phone.id
            and phone.provider_number_id is not None
            and phone.provider_number_id.strip()
        )
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
            number_provisioned=number_provisioned,
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
            activation=self._activation_response(activation, now=evaluated_at),
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
                provider_ready=number_provisioned,
                provisioning_status=(
                    provisioning.status if provisioning is not None else None
                ),
                can_retry=bool(provisioning is not None and provisioning.can_retry),
            ),
            forwarding=self._forwarding_guide(profile, phone),
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
    def _forwarding_guide(
        profile: BusinessProfile | None,
        phone: PhoneNumber | None,
    ) -> ForwardingGuide | None:
        if profile is None or profile.confirmed_carrier is None or phone is None:
            return None
        number_type = profile.detected_number_type
        if number_type is None:
            number_type = ActivationSnapshotService._classify_existing_number(
                profile.existing_phone_e164
            )
        return ForwardingInstructionCatalog().for_profile(
            carrier=cast(CarrierCode, profile.confirmed_carrier),
            number_type=number_type,
            presvo_number=phone.e164,
        )

    @staticmethod
    def _classify_existing_number(existing_phone_e164: str | None) -> str:
        if existing_phone_e164 is None:
            return "unknown"
        try:
            parsed = phonenumbers.parse(existing_phone_e164, None)
        except phonenumbers.NumberParseException:
            return "unknown"
        if not phonenumbers.is_valid_number_for_region(parsed, "FR"):
            return "unknown"
        number_type = phonenumbers.number_type(parsed)
        if number_type == phonenumbers.PhoneNumberType.MOBILE:
            return "mobile"
        if number_type == phonenumbers.PhoneNumberType.FIXED_LINE:
            return "fixed"
        return "unknown"

    @staticmethod
    def _activation_response(
        activation: CustomerActivation | None,
        *,
        now: datetime | None = None,
    ) -> ActivationProgressResponse:
        verification_status = (
            activation.verification_status
            if activation is not None
            else "not_started"
        )
        if (
            activation is not None
            and verification_status in {"open", "claimed"}
            and activation.verification_window_expires_at is not None
        ):
            deadline = as_utc(activation.verification_window_expires_at)
            if verification_status == "claimed":
                deadline += COMPLETION_GRACE
            if as_utc(now or datetime.now(UTC)) >= deadline:
                verification_status = "expired"
        return ActivationProgressResponse(
            profile_confirmed_at=ActivationSnapshotService._optional_utc(
                activation.profile_confirmed_at if activation is not None else None
            ),
            provisioning_consented_at=ActivationSnapshotService._optional_utc(
                activation.provisioning_consented_at
                if activation is not None
                else None
            ),
            verification_window_started_at=ActivationSnapshotService._optional_utc(
                activation.verification_window_started_at
                if activation is not None
                else None
            ),
            verification_window_expires_at=ActivationSnapshotService._optional_utc(
                activation.verification_window_expires_at
                if activation is not None
                else None
            ),
            verification_status=verification_status,
            forwarding_verified_at=ActivationSnapshotService._optional_utc(
                activation.forwarding_verified_at if activation is not None else None
            ),
            go_live_approved_at=ActivationSnapshotService._optional_utc(
                activation.go_live_approved_at if activation is not None else None
            ),
            activated_at=ActivationSnapshotService._optional_utc(
                activation.activated_at if activation is not None else None
            ),
            last_failure_code=(
                activation.last_failure_code if activation is not None else None
            ),
        )

    @staticmethod
    def _optional_utc(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return CustomerReadinessPolicy._as_utc(value)

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
            as_utc(activation.verification_window_started_at)
            <= now
            < as_utc(activation.verification_window_expires_at)
        )

    @staticmethod
    def _require_session(session: AsyncSession | None) -> AsyncSession:
        if session is None:
            raise ValueError("session is required when repositories are not provided")
        return session
