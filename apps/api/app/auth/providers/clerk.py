import math
from typing import Any

import jwt
from fastapi import HTTPException, status

from app.auth.domain import ExternalIdentity
from app.auth.domain import ExternalUserProfile
from app.auth.providers.base import AuthProvider
from app.core.auth_failures import (
    AuthenticationUnavailable,
    TokenRejected,
    TokenRejectionReason,
)
from app.auth.jwks import SigningKeyResolver
from app.core.config import Settings
from app.core.observability import Observability


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

    async def verify_token(self, token: str) -> ExternalIdentity:
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
            identity = ExternalIdentity(external_user_id=subject)
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
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Webhook secret not configured",
            )

        from app.core.webhook_verifier import verify_svix_signature

        return verify_svix_signature(
            secret=self.settings.clerk_webhook_secret,
            payload=payload,
            headers=headers,
        )


def extract_clerk_user_profile(payload: dict[str, Any]) -> ExternalUserProfile:
    external_user_id = payload.get("id")
    email_addresses = payload.get("email_addresses", [])
    if type(external_user_id) is not str or not external_user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing user identifier",
        )
    if not isinstance(email_addresses, list) or not email_addresses:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing email address",
        )
    first_email = email_addresses[0]
    email = (
        first_email.get("email_address")
        if isinstance(first_email, dict)
        else None
    )
    if type(email) is not str or not email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing email address",
        )
    return ExternalUserProfile(
        external_user_id=external_user_id,
        email=email,
    )
