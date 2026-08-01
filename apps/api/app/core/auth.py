import hmac
import logging
import math
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth_failures import (
    AuthenticationUnavailable,
    TokenRejected,
    TokenRejectionReason,
)
from app.core.clerk_jwks import (
    JwksSigningKeyResolver,
    SigningKeyResolver,
    StaticSigningKeyResolver,
)
from app.core.config import Settings
from app.core.database import get_session
from app.core.http_origin import parse_canonical_http_origins
from app.core.observability import Observability
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
    async def verify_token(self, token: str) -> UserIdentity:
        raise NotImplementedError

    async def get_user_id(self, token: str) -> str:
        return (await self.verify_token(token)).clerk_user_id

    async def aclose(self) -> None:
        return None


class ClerkAuthProvider(AuthProvider):
    def __init__(
        self,
        *,
        settings: Settings,
        authorized_parties: frozenset[str],
        signing_key_resolver: SigningKeyResolver,
        observability: Observability,
    ) -> None:
        self.settings = settings
        self._authorized_parties = authorized_parties
        self._signing_key_resolver = signing_key_resolver
        self._observability = observability

    async def verify_token(self, token: str) -> UserIdentity:
        try:
            signing_key = await self._signing_key_resolver.resolve_key(token)
            configured_audience_is_present = bool(self.settings.clerk_audience)
            decode_kwargs: dict[str, Any] = {
                "key": signing_key,
                "algorithms": ["RS256"],
                "issuer": self.settings.clerk_issuer,
                "options": {
                    "require": ["exp", "nbf", "sub", "azp"],
                    "verify_aud": configured_audience_is_present,
                },
            }
            if configured_audience_is_present:
                decode_kwargs["audience"] = self.settings.clerk_audience
            try:
                payload = jwt.decode(token, **decode_kwargs)
            except jwt.DecodeError as error:
                if type(error) is not jwt.DecodeError:
                    raise
                relaxed_decode_kwargs = {
                    **decode_kwargs,
                    "options": {
                        **decode_kwargs["options"],
                        "verify_exp": False,
                        "verify_nbf": False,
                    },
                }
                relaxed_payload = jwt.decode(token, **relaxed_decode_kwargs)
                if not self._has_valid_numeric_date_types(relaxed_payload):
                    raise TokenRejected("claims") from None
                raise
            except (TypeError, ValueError, OverflowError):
                raise TokenRejected("claims") from None
            if not self._has_valid_numeric_date_types(payload):
                raise TokenRejected("claims")
            subject: object = payload.get("sub")
            if (
                not isinstance(subject, str)
                or type(subject) is not str
                or not subject
            ):
                raise TokenRejected("claims")
            authorized_party = payload.get("azp")
            if (
                type(authorized_party) is not str
                or authorized_party not in self._authorized_parties
            ):
                raise TokenRejected("authorized_party")
            identity = UserIdentity(clerk_user_id=subject)
        except AuthenticationUnavailable as error:
            self._observability.record_auth_verification(
                "unavailable", error.reason
            )
            raise
        except TokenRejected as error:
            self._observability.record_auth_verification("rejected", error.reason)
            raise
        except jwt.InvalidAlgorithmError:
            self._raise_token_rejected("algorithm")
        except (jwt.InvalidSignatureError, jwt.InvalidKeyError):
            self._raise_token_rejected("signature")
        except jwt.InvalidIssuerError:
            self._raise_token_rejected("issuer")
        except jwt.InvalidAudienceError:
            self._raise_token_rejected("audience")
        except jwt.MissingRequiredClaimError as error:
            self._raise_token_rejected(
                "authorized_party" if error.claim == "azp" else "claims"
            )
        except (
            jwt.ExpiredSignatureError,
            jwt.ImmatureSignatureError,
            jwt.InvalidIssuedAtError,
            jwt.exceptions.InvalidSubjectError,
        ):
            self._raise_token_rejected("claims")
        except jwt.DecodeError:
            self._raise_token_rejected("malformed")
        except jwt.PyJWTError:
            self._raise_token_rejected("malformed")

        self._observability.record_auth_verification("accepted", "none")
        return identity

    @staticmethod
    def _has_valid_numeric_date_types(payload: dict[str, Any]) -> bool:
        for claim in ("exp", "nbf"):
            value = payload.get(claim)
            if type(value) is int:
                continue
            if type(value) is float and math.isfinite(value):
                continue
            return False
        return True

    def _raise_token_rejected(self, reason: TokenRejectionReason) -> None:
        self._observability.record_auth_verification("rejected", reason)
        raise TokenRejected(reason) from None

    async def aclose(self) -> None:
        await self._signing_key_resolver.aclose()

    def verify_webhook(self, payload: bytes, headers: dict[str, str]) -> str:
        if not self.settings.clerk_webhook_secret:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Webhook secret not configured")

        from app.core.webhook_verifier import verify_svix_signature

        return verify_svix_signature(
            secret=self.settings.clerk_webhook_secret,
            payload=payload,
            headers=headers,
        )


class LocalAuthProvider(AuthProvider):
    def __init__(self, *, token: str) -> None:
        self._token = token.encode("utf-8")

    async def verify_token(self, token: str) -> UserIdentity:
        if not hmac.compare_digest(token.encode("utf-8"), self._token):
            raise TokenRejected("signature")
        return UserIdentity(clerk_user_id=LOCAL_USER_EXTERNAL_ID)


bearer_scheme = HTTPBearer(auto_error=False)


def get_auth_provider(request: Request) -> AuthProvider:
    auth_provider = getattr(request.app.state, "auth_provider", None)
    if auth_provider is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Authentication provider not initialized",
        )
    return auth_provider


def build_auth_provider(
    *,
    settings: Settings,
    observability: Observability,
) -> AuthProvider:
    if settings.auth_mode == "local":
        return LocalAuthProvider(token=settings.local_auth_token)
    authorized_parties = frozenset(
        parse_canonical_http_origins(settings.clerk_authorized_parties)
    )
    if settings.clerk_jwt_key:
        resolver: SigningKeyResolver = StaticSigningKeyResolver(
            settings.clerk_jwt_key
        )
    else:
        resolver = JwksSigningKeyResolver(
            jwks_url=str(settings.clerk_jwks_url),
            cache_ttl_seconds=settings.clerk_jwks_cache_ttl_seconds,
            stale_grace_seconds=settings.clerk_jwks_stale_grace_seconds,
            connect_timeout_seconds=settings.clerk_jwks_connect_timeout_seconds,
            read_timeout_seconds=settings.clerk_jwks_read_timeout_seconds,
            pool_timeout_seconds=settings.clerk_jwks_pool_timeout_seconds,
            total_timeout_seconds=settings.clerk_jwks_total_timeout_seconds,
            observability=observability,
        )
    return ClerkAuthProvider(
        settings=settings,
        authorized_parties=authorized_parties,
        signing_key_resolver=resolver,
        observability=observability,
    )


async def require_user_identity(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    session: AsyncSession = Depends(get_session),
    auth_provider: AuthProvider = Depends(get_auth_provider),
) -> AuthenticatedUserIdentity:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )

    try:
        identity = await auth_provider.verify_token(credentials.credentials)
    except TokenRejected as error:
        logger.warning(
            "event=clerk_token_rejected operation=verify_token reason=%s",
            error.reason,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        ) from None
    except AuthenticationUnavailable as error:
        logger.warning(
            "event=authentication_unavailable operation=verify_token reason=%s",
            error.reason,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication temporarily unavailable",
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
