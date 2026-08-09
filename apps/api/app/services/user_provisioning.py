from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.domain import ExternalUserProfile
from app.models.user import User
from app.repositories.agent_config_repository import AgentConfigRepository
from app.repositories.business_profile_repository import BusinessProfileRepository
from app.repositories.customer_activation_repository import (
    CustomerActivationRepository,
)
from app.repositories.user_repository import UserRepository


class UserProvisioning:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.user_repository = UserRepository(session)
        self.agent_config_repository = AgentConfigRepository(session)
        self.business_profile_repository = BusinessProfileRepository(session)
        self.customer_activation_repository = CustomerActivationRepository(session)

    async def ensure_user(self, profile: ExternalUserProfile) -> User:
        await self.user_repository.acquire_bootstrap_lock(
            external_user_id=profile.external_user_id
        )
        user = await self.user_repository.get_by_external_user_id(
            profile.external_user_id
        )
        if user is None:
            user = await self.user_repository.create(
                external_user_id=profile.external_user_id,
                email=profile.email,
            )

        await self.agent_config_repository.get_or_create_default(user.id)
        await self.business_profile_repository.get_or_create_for_update(user.id)
        await self.customer_activation_repository.get_or_create_for_update(user.id)
        await self.session.flush()
        return user
