import hmac
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.database import get_session
from app.core.logging import report_safe_exception
from app.repositories.user_repository import UserRepository
from app.services.user_bootstrap_service import UserBootstrapService

logger = logging.getLogger(__name__)
LOCAL_USER_EXTERNAL_ID = "local_presvo_user"
LOCAL_USER_EMAIL = "local@presvo.invalid"


@dataclass(slots=True)
class UserIdentity:
    clerk_user_id: str
    internal_user_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class AuthenticatedUserIdentity:
    clerk_user_id: str
    internal_user_id: UUID


class AuthProvider(ABC):
    @abstractmethod
    def verify_token(self, token: str) -> UserIdentity:
        raise NotImplementedError

    def get_user_id(self, token: str) -> str:
        return self.verify_token(token).clerk_user_id


class ClerkAuthProvider(AuthProvider):
    def __init__(
        self,
        *,
        settings: Settings | None = None,
        jwk_client: jwt.PyJWKClient | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._jwk_client = jwk_client

    def verify_token(self, token: str) -> UserIdentity:
        decode_kwargs: dict[str, Any] = {
            "algorithms": ["RS256"],
            "issuer": self.settings.clerk_issuer,
        }
        if self.settings.clerk_jwt_key:
            decode_kwargs["key"] = self.settings.clerk_jwt_key
        else:
            jwks_url = self._resolve_jwks_url()
            signing_key = self._get_jwk_client(jwks_url).get_signing_key_from_jwt(token)
            decode_kwargs["key"] = signing_key.key
        if self.settings.clerk_audience:
            decode_kwargs["audience"] = self.settings.clerk_audience
        else:
            decode_kwargs["options"] = {"verify_aud": False}

        payload = jwt.decode(token, **decode_kwargs)
        subject = payload.get("sub")
        if not subject:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing subject")

        return UserIdentity(clerk_user_id=subject)

    def verify_webhook(self, payload: bytes, headers: dict[str, str]) -> str:
        if not self.settings.clerk_webhook_secret:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Webhook secret not configured")

        from app.core.webhook_verifier import verify_svix_signature

        return verify_svix_signature(
            secret=self.settings.clerk_webhook_secret,
            payload=payload,
            headers=headers,
        )

    def _resolve_jwks_url(self) -> str:
        if self.settings.clerk_jwks_url:
            return self.settings.clerk_jwks_url
        if self.settings.clerk_issuer:
            return self.settings.clerk_issuer.rstrip("/") + "/.well-known/jwks.json"
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="JWT key not configured")

    def _get_jwk_client(self, jwks_url: str) -> jwt.PyJWKClient:
        if self._jwk_client is not None:
            return self._jwk_client
        self._jwk_client = jwt.PyJWKClient(jwks_url)
        return self._jwk_client


class LocalAuthProvider(AuthProvider):
    def __init__(self, *, token: str) -> None:
        self._token = token.encode("utf-8")

    def verify_token(self, token: str) -> UserIdentity:
        if not hmac.compare_digest(token.encode("utf-8"), self._token):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token",
            )
        return UserIdentity(clerk_user_id=LOCAL_USER_EXTERNAL_ID)


bearer_scheme = HTTPBearer(auto_error=False)


def get_auth_provider(request: Request) -> AuthProvider:
    settings = getattr(request.app.state, "settings", None) or get_settings()
    if settings.auth_mode == "local":
        return LocalAuthProvider(token=settings.local_auth_token)
    return ClerkAuthProvider(settings=settings)


async def require_user_identity(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    session: AsyncSession = Depends(get_session),
    auth_provider: AuthProvider = Depends(get_auth_provider),
) -> AuthenticatedUserIdentity:
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")

    try:
        identity = auth_provider.verify_token(credentials.credentials)
    except jwt.PyJWTError as exc:
        report_safe_exception(
            logger,
            event="clerk_token_rejected",
            operation="verify_token",
            error=exc,
            level=logging.WARNING,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        ) from None

    if isinstance(auth_provider, LocalAuthProvider):
        local_user = await UserBootstrapService(session).ensure_user(
            external_user_id=LOCAL_USER_EXTERNAL_ID,
            email=LOCAL_USER_EMAIL,
        )
        await session.commit()
        internal_user_id = local_user.id
    else:
        synced_user = await UserRepository(session).get_by_clerk_user_id(
            identity.clerk_user_id
        )
        if synced_user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not synced",
            )
        internal_user_id = synced_user.id

    return AuthenticatedUserIdentity(
        clerk_user_id=identity.clerk_user_id,
        internal_user_id=internal_user_id,
    )


def extract_primary_email(payload: dict[str, Any]) -> str:
    email_addresses = payload.get("email_addresses", [])
    if not email_addresses:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing email address")

    email = email_addresses[0].get("email_address")
    if not email:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing email address")
    return email
