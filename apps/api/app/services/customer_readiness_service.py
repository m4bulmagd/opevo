from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.agent_config import AgentConfig
from app.models.business_profile import BusinessProfile
from app.models.customer_activation import CustomerActivation
from app.models.phone_number import PhoneNumber
from app.models.phone_number_provisioning import PhoneNumberProvisioning
from app.models.subscription import Subscription
from app.models.user import User
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
from app.services.business_profile_service import REQUIRED_PROFILE_FIELDS
from app.services.customer_readiness_policy import (
    CustomerReadinessPolicy,
    CustomerReadinessResult,
    CustomerReadinessSnapshot,
)
from app.services.routing_fingerprint import routing_fingerprint


@dataclass(frozen=True, slots=True)
class CustomerReadinessContext:
    result: CustomerReadinessResult
    user: User | None
    subscription: Subscription | None
    balance: int
    phone_number: PhoneNumber | None
    provisioning: PhoneNumberProvisioning | None
    agent_config: AgentConfig | None
    business_profile: BusinessProfile | None
    activation: CustomerActivation | None


@dataclass(frozen=True, slots=True)
class ActivationReadinessPrerequisites:
    business_profile_complete: bool
    profile_projection_current: bool
    forwarding_verified: bool
    go_live_approved: bool


def business_profile_is_complete(profile: BusinessProfile | None) -> bool:
    if profile is None:
        return False
    return all(
        not _is_missing_required_value(getattr(profile, field))
        for field in REQUIRED_PROFILE_FIELDS
    )


def _is_missing_required_value(value: object) -> bool:
    return not value or isinstance(value, str) and not value.strip()


def activation_readiness_prerequisites(
    *,
    profile: BusinessProfile | None,
    activation: CustomerActivation | None,
    phone_number: PhoneNumber | None,
    agent_config: AgentConfig | None,
) -> ActivationReadinessPrerequisites:
    profile_complete = business_profile_is_complete(profile)
    projection_current = bool(
        profile is not None
        and agent_config is not None
        and agent_config.profile_projection_revision == profile.content_revision
    )
    current_fingerprint = (
        routing_fingerprint(profile, phone_number) if profile is not None else None
    )
    verified_fingerprint = (
        activation.verified_routing_fingerprint
        if activation is not None
        else None
    )
    forwarding_verified = bool(
        activation is not None
        and activation.forwarding_verified_at is not None
        and verified_fingerprint is not None
        and current_fingerprint is not None
        and verified_fingerprint == current_fingerprint
    )
    go_live_approved = bool(
        activation is not None and activation.go_live_approved_at is not None
    )
    return ActivationReadinessPrerequisites(
        business_profile_complete=profile_complete,
        profile_projection_current=projection_current,
        forwarding_verified=forwarding_verified,
        go_live_approved=go_live_approved,
    )


def build_customer_readiness_snapshot(
    *,
    user: User | None,
    subscription: Subscription | None,
    balance: int,
    phone_number: PhoneNumber | None,
    provisioning: PhoneNumberProvisioning | None,
    agent_config: AgentConfig | None,
    activation_required: bool = False,
    business_profile_complete: bool = False,
    profile_projection_current: bool = False,
    forwarding_verified: bool = False,
    go_live_approved: bool = False,
    agent_enabled_override: bool | None = None,
) -> CustomerReadinessSnapshot:
    return CustomerReadinessSnapshot(
        user_status=user.status if user is not None else None,
        plan_tier=subscription.plan_tier if subscription is not None else None,
        subscription_status=(
            subscription.status if subscription is not None else None
        ),
        current_period_start=(
            subscription.current_period_start if subscription is not None else None
        ),
        current_period_end=(
            subscription.current_period_end if subscription is not None else None
        ),
        balance=balance,
        provisioning_status=(
            provisioning.status if provisioning is not None else None
        ),
        phone_present=phone_number is not None,
        phone_provider_id_present=bool(
            phone_number is not None
            and phone_number.provider_number_id
            and phone_number.provider_number_id.strip()
        ),
        phone_active=bool(phone_number is not None and phone_number.is_active),
        phone_connection_name=(
            phone_number.provider_connection_name
            if phone_number is not None
            else None
        ),
        agent_present=agent_config is not None,
        agent_enabled=bool(
            agent_config is not None
            and (
                agent_enabled_override
                if agent_enabled_override is not None
                else agent_config.is_enabled
            )
        ),
        agent_name=(agent_config.agent_name if agent_config is not None else None),
        owner_context=(
            agent_config.owner_context if agent_config is not None else None
        ),
        system_prompt=(
            agent_config.system_prompt if agent_config is not None else None
        ),
        knowledge_base=(
            agent_config.knowledge_base if agent_config is not None else None
        ),
        activation_required=activation_required,
        business_profile_complete=business_profile_complete,
        profile_projection_current=profile_projection_current,
        forwarding_verified=forwarding_verified,
        go_live_approved=go_live_approved,
    )


def evaluate_customer_readiness(
    *,
    user: User | None,
    subscription: Subscription | None,
    balance: int,
    phone_number: PhoneNumber | None,
    provisioning: PhoneNumberProvisioning | None,
    agent_config: AgentConfig | None,
    business_profile: BusinessProfile | None = None,
    activation: CustomerActivation | None = None,
    activation_required: bool = False,
    agent_enabled_override: bool | None = None,
    go_live_approved_override: bool | None = None,
    now: datetime | None = None,
) -> CustomerReadinessResult:
    prerequisites = activation_readiness_prerequisites(
        profile=business_profile,
        activation=activation,
        phone_number=phone_number,
        agent_config=agent_config,
    )
    go_live_approved = prerequisites.go_live_approved
    if go_live_approved_override is not None:
        go_live_approved = go_live_approved_override
    return CustomerReadinessPolicy.evaluate(
        build_customer_readiness_snapshot(
            user=user,
            subscription=subscription,
            balance=balance,
            phone_number=phone_number,
            provisioning=provisioning,
            agent_config=agent_config,
            activation_required=activation_required,
            business_profile_complete=prerequisites.business_profile_complete,
            profile_projection_current=prerequisites.profile_projection_current,
            forwarding_verified=prerequisites.forwarding_verified,
            go_live_approved=go_live_approved,
            agent_enabled_override=agent_enabled_override,
        ),
        now=now,
    )


class CustomerReadinessService:
    def __init__(
        self,
        session: AsyncSession | None = None,
        *,
        user_repository: UserRepository | None = None,
        subscription_repository: SubscriptionRepository | None = None,
        usage_repository: UsageRepository | None = None,
        phone_number_repository: PhoneNumberRepository | None = None,
        provisioning_repository: PhoneNumberProvisioningRepository | None = None,
        agent_config_repository: AgentConfigRepository | None = None,
        business_profile_repository: BusinessProfileRepository | None = None,
        activation_repository: CustomerActivationRepository | None = None,
        activation_flow_enabled: bool | None = None,
    ) -> None:
        if activation_flow_enabled is None:
            activation_flow_enabled = get_settings().activation_flow_enabled
        if user_repository is None:
            user_repository = UserRepository(self._require_session(session))
        if subscription_repository is None:
            subscription_repository = SubscriptionRepository(
                self._require_session(session)
            )
        if usage_repository is None:
            usage_repository = UsageRepository(self._require_session(session))
        if phone_number_repository is None:
            phone_number_repository = PhoneNumberRepository(
                self._require_session(session)
            )
        if provisioning_repository is None:
            provisioning_repository = PhoneNumberProvisioningRepository(
                self._require_session(session)
            )
        if agent_config_repository is None:
            agent_config_repository = AgentConfigRepository(
                self._require_session(session)
            )
        if activation_flow_enabled and business_profile_repository is None:
            business_profile_repository = BusinessProfileRepository(
                self._require_session(session)
            )
        if activation_flow_enabled and activation_repository is None:
            activation_repository = CustomerActivationRepository(
                self._require_session(session)
            )

        self.user_repository = user_repository
        self.subscription_repository = subscription_repository
        self.usage_repository = usage_repository
        self.phone_number_repository = phone_number_repository
        self.provisioning_repository = provisioning_repository
        self.agent_config_repository = agent_config_repository
        self.business_profile_repository = business_profile_repository
        self.activation_repository = activation_repository
        self.activation_flow_enabled = activation_flow_enabled

    async def evaluate(
        self,
        user_id,
        *,
        agent_config_override: AgentConfig | None = None,
        now: datetime | None = None,
    ) -> CustomerReadinessContext:
        user = await self.user_repository.get_by_id(user_id)
        subscription = await self.subscription_repository.get_by_user_id(user_id)
        balance = await self.usage_repository.get_current_balance(user_id=user_id)
        phone_number = await self.phone_number_repository.get_by_user_id(user_id)
        provisioning = await self.provisioning_repository.get_by_user_id(user_id)
        agent_config = agent_config_override
        if agent_config is None:
            agent_config = await self.agent_config_repository.get_by_user_id(user_id)

        business_profile = None
        activation = None
        if self.activation_flow_enabled:
            if self.business_profile_repository is None or self.activation_repository is None:
                raise RuntimeError("activation repositories are required")
            business_profile = await self.business_profile_repository.get_by_user_id(
                user_id
            )
            activation = await self.activation_repository.get_by_user_id(user_id)

        result = evaluate_customer_readiness(
            user=user,
            subscription=subscription,
            balance=balance,
            phone_number=phone_number,
            provisioning=provisioning,
            agent_config=agent_config,
            business_profile=business_profile,
            activation=activation,
            activation_required=self.activation_flow_enabled,
            now=now,
        )
        return CustomerReadinessContext(
            result=result,
            user=user,
            subscription=subscription,
            balance=balance,
            phone_number=phone_number,
            provisioning=provisioning,
            agent_config=agent_config,
            business_profile=business_profile,
            activation=activation,
        )

    @staticmethod
    def _require_session(session: AsyncSession | None) -> AsyncSession:
        if session is None:
            raise ValueError("session is required when repositories are not provided")
        return session
