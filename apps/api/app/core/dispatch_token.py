import time

import jwt

from app.core.config import get_settings

ALGORITHM = "HS256"


def create_dispatch_token(*, call_id: str, user_id: str) -> str | None:
    settings = get_settings()
    secret = settings.agent_dispatch_jwt_secret
    if not secret:
        return None
    payload = {
        "call_id": call_id,
        "user_id": user_id,
        "iat": int(time.time()),
        "exp": int(time.time()) + settings.agent_dispatch_jwt_ttl_seconds,
    }
    return jwt.encode(payload, secret, algorithm=ALGORITHM)


def verify_dispatch_token(token: str, *, expected_call_id: str) -> dict:
    settings = get_settings()
    secret = settings.agent_dispatch_jwt_secret
    if not secret:
        raise ValueError("agent_dispatch_jwt_secret is not configured")
    payload = jwt.decode(token, secret, algorithms=[ALGORITHM])
    if payload.get("call_id") != expected_call_id:
        raise ValueError(
            f"Token call_id {payload.get('call_id')!r} does not match expected {expected_call_id!r}"
        )
    return payload
