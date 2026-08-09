from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.domain import AuthenticatedUser
from app.auth.providers.base import AuthProvider
from app.services.authentication_service import AuthenticationService


class SessionAuthenticator:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        auth_provider: AuthProvider,
    ) -> None:
        self.session_factory = session_factory
        self.auth_provider = auth_provider

    async def authenticate(self, token: str) -> AuthenticatedUser:
        async with self.session_factory() as session:
            return await AuthenticationService(
                session=session,
                auth_provider=self.auth_provider,
            ).authenticate(token)
