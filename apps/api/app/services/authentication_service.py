from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.domain import AuthenticatedUser
from app.auth.failures import UserNotProvisioned
from app.auth.providers.base import AuthProvider
from app.repositories.user_repository import UserRepository
from app.services.user_provisioning import UserProvisioning


class AuthenticationService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        auth_provider: AuthProvider,
    ) -> None:
        self.session = session
        self.auth_provider = auth_provider
        self.user_repository = UserRepository(session)
        self.user_provisioning = UserProvisioning(session)

    async def authenticate(self, token: str) -> AuthenticatedUser:
        identity = await self.auth_provider.verify_token(token)
        user = await self.user_repository.get_by_external_user_id(
            identity.external_user_id
        )
        if user is None:
            if identity.bootstrap_profile is None:
                raise UserNotProvisioned
            user = await self.user_provisioning.ensure_user(
                identity.bootstrap_profile
            )
            await self.session.commit()
        return AuthenticatedUser(internal_user_id=user.id)
