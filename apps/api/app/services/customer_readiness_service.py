from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_config import AgentConfig
from app.models.phone_number import PhoneNumber
from app.models.phone_number_provisioning import PhoneNumberProvisioning
from app.models.subscription import Subscription
from app.models.user import User
from app.repositories.agent_config_repository import AgentConfigRepository
from app.repositories.phone_number_provisioning_repository import (
    PhoneNumberProvisioningRepository,
)
from app.repositories.phone_number_repository import PhoneNumberRepository
from app.repositories.subscription_repository import SubscriptionRepository
from app.repositories.usage_repository import UsageRepository
from app.repositories.user_repository import UserRepository
from app.services.customer_readiness_policy import (
    CustomerReadinessPolicy,
    CustomerReadinessResult,
    CustomerReadinessSnapshot,
)


@dataclass(frozen=True, slots=True)
class CustomerReadinessContext:
    result: CustomerReadinessResult
    user: User | None
    subscription: Subscription | None
    balance: int
    phone_number: PhoneNumber | None
    provisioning: PhoneNumberProvisioning | None
    agent_config: AgentConfig | None


def build_customer_readiness_snapshot(
    *,
    user: User | None,
    subscription: Subscription | None,
    balance: int,
    phone_number: PhoneNumber | None,
    provisioning: PhoneNumberProvisioning | None,
    agent_config: AgentConfig | None,
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
        agent_enabled=bool(agent_config is not None and agent_config.is_enabled),
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
    ) -> None:
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

        self.user_repository = user_repository
        self.subscription_repository = subscription_repository
        self.usage_repository = usage_repository
        self.phone_number_repository = phone_number_repository
        self.provisioning_repository = provisioning_repository
        self.agent_config_repository = agent_config_repository

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

        snapshot = build_customer_readiness_snapshot(
            user=user,
            subscription=subscription,
            balance=balance,
            phone_number=phone_number,
            provisioning=provisioning,
            agent_config=agent_config,
        )
        result = CustomerReadinessPolicy.evaluate(snapshot, now=now)
        return CustomerReadinessContext(
            result=result,
            user=user,
            subscription=subscription,
            balance=balance,
            phone_number=phone_number,
            provisioning=provisioning,
            agent_config=agent_config,
        )

    @staticmethod
    def _require_session(session: AsyncSession | None) -> AsyncSession:
        if session is None:
            raise ValueError("session is required when repositories are not provided")
        return session
