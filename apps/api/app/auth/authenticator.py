from typing import Protocol

from app.auth.domain import AuthenticatedUser


class Authenticator(Protocol):
    async def authenticate(self, token: str) -> AuthenticatedUser: ...
