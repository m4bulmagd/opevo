import time
from uuid import UUID

import jwt

from app.core.dispatch_token import (
    ALGORITHM,
    DispatchTokenConfig,
)


VERIFICATION_AUDIENCE = "opevo-forwarding-verification"
DEFAULT_VERIFICATION_TOKEN_TTL_SECONDS = 900
MAX_VERIFICATION_TOKEN_TTL_SECONDS = 900
REQUIRED_VERIFICATION_CLAIMS = ("aud", "sub", "user_id", "iat", "exp")


class VerificationTokenError(ValueError):
    """A safe, caller-facing verification token validation error."""


class VerificationTokenConfigurationError(VerificationTokenError):
    """Verification token signing is not configured safely."""


def _identifier(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise VerificationTokenError("Invalid verification token")
    try:
        return str(UUID(value))
    except (TypeError, ValueError, AttributeError):
        raise VerificationTokenError("Invalid verification token") from None


def create_verification_token(
    *,
    session_id: str,
    user_id: str,
    config: DispatchTokenConfig,
    ttl_seconds: int = DEFAULT_VERIFICATION_TOKEN_TTL_SECONDS,
) -> str:
    normalized_session_id = _identifier(session_id)
    normalized_user_id = _identifier(user_id)
    if (
        isinstance(ttl_seconds, bool)
        or not isinstance(ttl_seconds, int)
        or ttl_seconds <= 0
        or ttl_seconds > MAX_VERIFICATION_TOKEN_TTL_SECONDS
    ):
        raise VerificationTokenConfigurationError(
            "Verification token lifetime is not configured safely"
        )
    issued_at = int(time.time())
    return jwt.encode(
        {
            "aud": VERIFICATION_AUDIENCE,
            "sub": normalized_session_id,
            "user_id": normalized_user_id,
            "iat": issued_at,
            "exp": issued_at + ttl_seconds,
        },
        config.secret,
        algorithm=ALGORITHM,
    )


def verify_verification_token(
    token: str,
    *,
    expected_session_id: str,
    expected_user_id: str,
    config: DispatchTokenConfig,
) -> dict:
    try:
        normalized_expected_session_id = _identifier(expected_session_id)
        normalized_expected_user_id = _identifier(expected_user_id)
        if not isinstance(token, str) or not token:
            raise VerificationTokenError("Invalid verification token")
        payload = jwt.decode(
            token,
            config.secret,
            algorithms=[ALGORITHM],
            audience=VERIFICATION_AUDIENCE,
            options={"require": list(REQUIRED_VERIFICATION_CLAIMS)},
        )
        session_id = _identifier(payload["sub"])
        user_id = _identifier(payload["user_id"])
        issued_at = payload["iat"]
        expires_at = payload["exp"]
        if (
            isinstance(issued_at, bool)
            or not isinstance(issued_at, int)
            or isinstance(expires_at, bool)
            or not isinstance(expires_at, int)
            or expires_at <= issued_at
            or expires_at - issued_at > MAX_VERIFICATION_TOKEN_TTL_SECONDS
            or session_id != normalized_expected_session_id
            or user_id != normalized_expected_user_id
        ):
            raise VerificationTokenError("Invalid verification token")
    except (jwt.PyJWTError, KeyError, TypeError, ValueError):
        raise VerificationTokenError("Invalid verification token") from None

    payload["sub"] = session_id
    payload["user_id"] = user_id
    return payload
