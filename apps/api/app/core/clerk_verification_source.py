from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class ClerkVerificationSource:
    kind: Literal["static", "jwks"]
    value: str


def select_clerk_verification_source(
    *,
    jwt_key: str | None,
    jwks_url: str | None,
) -> ClerkVerificationSource | None:
    static_key = jwt_key if jwt_key is not None and jwt_key.strip() else None
    remote_url = jwks_url if jwks_url is not None and jwks_url.strip() else None
    if static_key is not None and remote_url is None:
        return ClerkVerificationSource(kind="static", value=static_key)
    if remote_url is not None and static_key is None:
        return ClerkVerificationSource(kind="jwks", value=remote_url)
    return None
