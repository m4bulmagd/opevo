import base64
import hashlib
import hmac
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_session
from app.repositories.user_repository import UserRepository


@dataclass(slots=True)
class UserIdentity:
    user_id: str


class AuthProvider(ABC):
    @abstractmethod
    def verify_token(self, token: str) -> UserIdentity:
        raise NotImplementedError

    def get_user_id(self, token: str) -> str:
        return self.verify_token(token).user_id


class ClerkAuthProvider(AuthProvider):
    def __init__(self) -> None:
        self.settings = get_settings()

    def verify_token(self, token: str) -> UserIdentity:
        if not self.settings.clerk_jwt_secret:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="JWT secret not configured")

        payload = jwt.decode(
            token,
            self.settings.clerk_jwt_secret,
            algorithms=["HS256"],
            issuer=self.settings.clerk_issuer,
            audience=self.settings.clerk_audience,
        )
        subject = payload.get("sub")
        if not subject:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing subject")

        return UserIdentity(user_id=subject)

    def verify_webhook(self, payload: bytes, headers: dict[str, str]) -> str:
        if not self.settings.clerk_webhook_secret:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Webhook secret not configured")

        signature = headers.get("svix-signature")
        event_id = headers.get("svix-id")
        if not signature or not event_id:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing webhook signature")

        expected = base64.b64encode(
            hmac.new(
                self.settings.clerk_webhook_secret.encode("utf-8"),
                payload,
                hashlib.sha256,
            ).digest()
        ).decode("utf-8")

        if not hmac.compare_digest(signature, expected):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid webhook signature")

        return event_id


bearer_scheme = HTTPBearer(auto_error=False)


def get_auth_provider() -> AuthProvider:
    return ClerkAuthProvider()


async def require_user_identity(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    session: AsyncSession = Depends(get_session),
    auth_provider: AuthProvider = Depends(get_auth_provider),
) -> UserIdentity:
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")

    try:
        identity = auth_provider.verify_token(credentials.credentials)
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token") from exc

    user = await UserRepository(session).get_by_clerk_user_id(identity.user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not synced")

    return identity


def extract_primary_email(payload: dict[str, Any]) -> str:
    email_addresses = payload.get("email_addresses", [])
    if not email_addresses:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing email address")

    email = email_addresses[0].get("email_address")
    if not email:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing email address")
    return email
