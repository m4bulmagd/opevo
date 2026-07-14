import time
from typing import TypeGuard
from uuid import UUID

import jwt

from app.core.config import get_settings

ALGORITHM = "HS256"
REQUIRED_CLAIMS = ("call_id", "user_id", "agent_config_id", "iat", "exp")
MINIMUM_SECRET_BYTES = 32
UNSAFE_SECRET_MARKERS = (
    "change-me",
    "changeme",
    "replace-me",
    "replace-with",
    "your-secret",
)


class DispatchTokenError(ValueError):
    """A safe, caller-facing dispatch token validation error."""


class DispatchTokenConfigurationError(DispatchTokenError):
    """Dispatch token signing or verification is not configured safely."""


def is_dispatch_secret_safe(secret: object) -> TypeGuard[str]:
    if not isinstance(secret, str):
        return False
    normalized = secret.strip()
    if len(normalized.encode("utf-8")) < MINIMUM_SECRET_BYTES:
        return False
    lowered = normalized.casefold().replace("_", "-")
    return not any(marker in lowered for marker in UNSAFE_SECRET_MARKERS)


def _configured_secret() -> str:
    settings = get_settings()
    secret = settings.agent_dispatch_jwt_secret
    if not is_dispatch_secret_safe(secret):
        raise DispatchTokenConfigurationError(
            "Dispatch token signing is not configured safely"
        )
    return secret.strip()


def _identifier(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DispatchTokenError("Invalid dispatch token")
    try:
        return str(UUID(value))
    except (TypeError, ValueError, AttributeError):
        raise DispatchTokenError("Invalid dispatch token") from None


def create_dispatch_token(
    call_id: str,
    user_id: str,
    agent_config_id: str,
) -> str:
    settings = get_settings()
    secret = _configured_secret()
    normalized_call_id = _identifier(call_id)
    normalized_user_id = _identifier(user_id)
    normalized_agent_config_id = _identifier(agent_config_id)
    ttl_seconds = settings.agent_dispatch_jwt_ttl_seconds
    if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, int) or ttl_seconds <= 0:
        raise DispatchTokenConfigurationError(
            "Dispatch token lifetime is not configured safely"
        )
    issued_at = int(time.time())
    payload = {
        "call_id": normalized_call_id,
        "user_id": normalized_user_id,
        "agent_config_id": normalized_agent_config_id,
        "iat": issued_at,
        "exp": issued_at + ttl_seconds,
    }
    return jwt.encode(payload, secret, algorithm=ALGORITHM)


def verify_dispatch_token(
    token: str,
    expected_call_id: str,
    expected_user_id: str | None = None,
) -> dict:
    secret = _configured_secret()
    try:
        normalized_expected_call_id = _identifier(expected_call_id)
        normalized_expected_user_id = (
            _identifier(expected_user_id) if expected_user_id is not None else None
        )
        if not isinstance(token, str) or not token:
            raise DispatchTokenError("Invalid dispatch token")
        payload = jwt.decode(
            token,
            secret,
            algorithms=[ALGORITHM],
            options={"require": list(REQUIRED_CLAIMS)},
        )
        call_id = _identifier(payload["call_id"])
        user_id = _identifier(payload["user_id"])
        agent_config_id = _identifier(payload["agent_config_id"])
        issued_at = payload["iat"]
        expires_at = payload["exp"]
        if (
            isinstance(issued_at, bool)
            or not isinstance(issued_at, int)
            or isinstance(expires_at, bool)
            or not isinstance(expires_at, int)
            or expires_at <= issued_at
            or call_id != normalized_expected_call_id
            or (
                normalized_expected_user_id is not None
                and user_id != normalized_expected_user_id
            )
        ):
            raise DispatchTokenError("Invalid dispatch token")
    except DispatchTokenConfigurationError:
        raise
    except (jwt.PyJWTError, KeyError, TypeError, ValueError):
        raise DispatchTokenError("Invalid dispatch token") from None

    payload["call_id"] = call_id
    payload["user_id"] = user_id
    payload["agent_config_id"] = agent_config_id
    return payload
