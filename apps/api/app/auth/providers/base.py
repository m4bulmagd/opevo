from abc import ABC, abstractmethod

from app.auth.domain import ExternalIdentity


class AuthProvider(ABC):
    @abstractmethod
    async def verify_token(self, token: str) -> ExternalIdentity:
        raise NotImplementedError

    async def aclose(self) -> None:
        return None
