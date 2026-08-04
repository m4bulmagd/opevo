from collections.abc import Sequence

from app.core.clerk_verification_source import select_clerk_verification_source
from app.core.config import Settings
from app.core.dispatch_token import is_dispatch_secret_safe
from app.core.http_origin import (
    parse_canonical_http_origins,
    parse_http_origin,
    validate_absolute_https_url,
)


PRODUCTION_REQUIRED_SETTINGS = (
    "database_url",
    "redis_url",
    "clerk_issuer",
    "clerk_webhook_secret",
    "stripe_secret_key",
    "stripe_webhook_secret",
    "stripe_price_starter",
    "stripe_checkout_success_url",
    "stripe_checkout_cancel_url",
    "stripe_billing_portal_return_url",
    "stripe_billing_portal_configuration_id",
    "livekit_url",
    "livekit_api_key",
    "livekit_api_secret",
    "telnyx_api_key",
    "telnyx_active_connection_id",
    "telnyx_disabled_connection_id",
    "storage_bucket_name",
    "s3_endpoint_url",
    "s3_access_key",
    "s3_secret_key",
    "s3_region",
    "agent_dispatch_jwt_secret",
    "summary_provider",
    "summary_model",
)

WORKER_PRODUCTION_REQUIRED_SETTINGS = (
    "database_url",
    "redis_url",
    "livekit_url",
    "livekit_api_key",
    "livekit_api_secret",
    "telnyx_api_key",
    "telnyx_active_connection_id",
    "telnyx_disabled_connection_id",
    "storage_bucket_name",
    "s3_endpoint_url",
    "s3_access_key",
    "s3_secret_key",
    "s3_region",
    "agent_dispatch_jwt_secret",
    "summary_provider",
    "summary_model",
)


def _is_missing(value: object) -> bool:
    if value is None:
        return True
    return isinstance(value, str) and not value.strip()


def _require(settings: Settings, names: Sequence[str]) -> list[str]:
    return [name.upper() for name in names if _is_missing(getattr(settings, name))]


def validate_api_runtime(settings: Settings) -> None:
    environment = settings.app_env.strip().lower()
    if settings.auth_mode == "local" and (
        _is_missing(settings.local_auth_token)
        or settings.local_auth_token != settings.local_auth_token.strip()
    ):
        raise RuntimeError(
            "Missing or invalid required runtime settings: LOCAL_AUTH_TOKEN"
        )
    if settings.auth_mode == "clerk":
        invalid_clerk: list[str] = []
        if _is_missing(settings.clerk_issuer):
            invalid_clerk.append("CLERK_ISSUER")
        try:
            parse_canonical_http_origins(settings.clerk_authorized_parties)
        except ValueError:
            invalid_clerk.append("CLERK_AUTHORIZED_PARTIES")
        verification_source = select_clerk_verification_source(
            jwt_key=settings.clerk_jwt_key,
            jwks_url=settings.clerk_jwks_url,
        )
        if verification_source is None:
            invalid_clerk.append("exactly one of CLERK_JWT_KEY or CLERK_JWKS_URL")
        elif verification_source.kind == "jwks":
            try:
                validate_absolute_https_url(verification_source.value)
            except ValueError:
                invalid_clerk.append("CLERK_JWKS_URL")
        if invalid_clerk:
            raise RuntimeError(
                "Missing or invalid required runtime settings: "
                + ", ".join(invalid_clerk)
            )
    if environment == "development":
        return

    invalid_modes: list[str] = []
    if settings.auth_mode != "clerk":
        invalid_modes.append("AUTH_MODE")
    if environment == "production":
        required_modes = {
            "BILLING_MODE": (settings.billing_mode, "stripe"),
            "CARRIER_LOOKUP_MODE": (settings.carrier_lookup_mode, "telnyx"),
            "TELEPHONY_MODE": (settings.telephony_mode, "telnyx"),
        }
        invalid_modes.extend(
            name
            for name, (configured, required) in required_modes.items()
            if configured != required
        )
    if invalid_modes:
        raise RuntimeError(
            f"Missing or invalid required runtime settings: {', '.join(invalid_modes)}"
        )

    _validate_dispatch_secret(settings)

    if environment != "production":
        return

    missing = _require(settings, PRODUCTION_REQUIRED_SETTINGS)

    if _is_missing(settings.clerk_jwt_key) and _is_missing(settings.clerk_jwks_url):
        missing.append("CLERK_JWT_KEY or CLERK_JWKS_URL")

    if not settings.telnyx_ordering_enabled:
        missing.append("TELNYX_ORDERING_ENABLED")

    if not _is_missing(settings.summary_provider):
        if settings.summary_provider == "gemini":
            missing.extend(_require(settings, ("gemini_api_key",)))
        else:
            missing.append("SUMMARY_PROVIDER")

    portal_return_url = settings.stripe_billing_portal_return_url
    if isinstance(portal_return_url, str) and portal_return_url.strip():
        try:
            parse_http_origin(portal_return_url)
        except ValueError:
            missing.append("STRIPE_BILLING_PORTAL_RETURN_URL")

    if missing:
        raise RuntimeError(
            f"Missing or invalid required production settings: {', '.join(missing)}"
        )


def validate_worker_runtime(settings: Settings) -> None:
    environment = settings.app_env.strip().lower()
    if settings.billing_mode == "stripe":
        missing_billing_settings = _require(settings, ("stripe_secret_key",))
        if missing_billing_settings:
            raise RuntimeError(
                "Missing or invalid required runtime settings: "
                f"{', '.join(missing_billing_settings)}"
            )

    if environment == "development":
        return

    if settings.auth_mode != "clerk":
        raise RuntimeError(
            "Missing or invalid required runtime settings: AUTH_MODE"
        )

    if environment == "production" and settings.telephony_mode != "telnyx":
        raise RuntimeError(
            "Missing or invalid required runtime settings: TELEPHONY_MODE"
        )

    _validate_dispatch_secret(settings)

    if environment != "production":
        return

    missing = _require(settings, WORKER_PRODUCTION_REQUIRED_SETTINGS)
    if not settings.telnyx_ordering_enabled:
        missing.append("TELNYX_ORDERING_ENABLED")

    if not _is_missing(settings.summary_provider):
        if settings.summary_provider == "gemini":
            missing.extend(_require(settings, ("gemini_api_key",)))
        else:
            missing.append("SUMMARY_PROVIDER")

    if missing:
        raise RuntimeError(
            f"Missing or invalid required production settings: {', '.join(missing)}"
        )


def _validate_dispatch_secret(settings: Settings) -> None:
    if not is_dispatch_secret_safe(settings.agent_dispatch_jwt_secret):
        raise RuntimeError(
            "Missing or invalid required runtime settings: "
            "AGENT_DISPATCH_JWT_SECRET"
        )
