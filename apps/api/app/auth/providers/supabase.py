from collections.abc import Collection

import jwt

from app.auth.domain import ExternalIdentity, ExternalUserProfile
from app.auth.jwks import SigningKeyResolver
from app.auth.providers.base import AuthProvider
from app.core.auth_failures import (
    AuthenticationUnavailable,
    TokenRejected,
    TokenRejectionReason,
)
from app.core.observability import Observability


class SupabaseAuthProvider(AuthProvider):
    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        signing_key_resolver: SigningKeyResolver,
        observability: Observability,
        algorithms: Collection[str] = ("ES256", "RS256"),
    ) -> None:
        self._issuer = issuer
        self._audience = audience
        self._signing_key_resolver = signing_key_resolver
        self._observability = observability
        self._algorithms = tuple(algorithms)

    async def verify_token(self, token: str) -> ExternalIdentity:
        try:
            signing_key = await self._signing_key_resolver.resolve_key(token)
            payload = jwt.decode(
                token,
                key=signing_key,
                algorithms=self._algorithms,
                issuer=self._issuer,
                audience=self._audience,
                options={
                    "require": [
                        "exp",
                        "iat",
                        "sub",
                        "email",
                        "aud",
                        "role",
                        "is_anonymous",
                    ]
                },
            )
            subject = payload.get("sub")
            email = payload.get("email")
            role = payload.get("role")
            is_anonymous = payload.get("is_anonymous")
            if type(subject) is not str or not subject:
                raise TokenRejected("claims")
            if type(email) is not str or not email:
                raise TokenRejected("claims")
            if type(role) is not str or role != "authenticated":
                raise TokenRejected("claims")
            if is_anonymous is not False:
                raise TokenRejected("claims")
            identity = ExternalIdentity(
                external_user_id=subject,
                bootstrap_profile=ExternalUserProfile(
                    external_user_id=subject,
                    email=email,
                ),
            )
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
        except (
            jwt.MissingRequiredClaimError,
            jwt.ExpiredSignatureError,
            jwt.ImmatureSignatureError,
            jwt.InvalidIssuedAtError,
            jwt.exceptions.InvalidSubjectError,
        ):
            self._raise_token_rejected("claims")
        except (jwt.DecodeError, jwt.PyJWTError, TypeError, ValueError, OverflowError):
            self._raise_token_rejected("malformed")

        self._observability.record_auth_verification("accepted", "none")
        return identity

    def _raise_token_rejected(self, reason: TokenRejectionReason) -> None:
        self._observability.record_auth_verification("rejected", reason)
        raise TokenRejected(reason) from None

    async def aclose(self) -> None:
        await self._signing_key_resolver.aclose()
