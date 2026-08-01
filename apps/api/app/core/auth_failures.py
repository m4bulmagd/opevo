from typing import Literal

TokenRejectionReason = Literal[
    "malformed",
    "algorithm",
    "signature",
    "issuer",
    "audience",
    "claims",
    "authorized_party",
    "signing_key",
]
AuthenticationUnavailableReason = Literal[
    "jwks_timeout",
    "jwks_http",
    "jwks_invalid",
    "jwks_closed",
]


class TokenRejected(Exception):
    def __init__(self, reason: TokenRejectionReason) -> None:
        super().__init__("token rejected")
        self.reason = reason


class AuthenticationUnavailable(Exception):
    def __init__(self, reason: AuthenticationUnavailableReason) -> None:
        super().__init__("authentication unavailable")
        self.reason = reason
