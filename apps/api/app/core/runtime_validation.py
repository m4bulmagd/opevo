from collections.abc import Sequence

from app.core.config import Settings
from app.core.dispatch_token import is_dispatch_secret_safe
from app.core.http_origin import parse_http_origin


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
    if environment == "development":
        return

    if not is_dispatch_secret_safe(settings.agent_dispatch_jwt_secret):
        raise RuntimeError(
            "Missing or invalid required runtime settings: "
            "AGENT_DISPATCH_JWT_SECRET"
        )

    if environment != "production":
        return

    missing = _require(settings, PRODUCTION_REQUIRED_SETTINGS)

    if _is_missing(settings.clerk_jwt_key) and _is_missing(settings.clerk_jwks_url):
        missing.append("CLERK_JWT_KEY or CLERK_JWKS_URL")

    if not _is_missing(settings.summary_provider):
        if settings.summary_provider == "gemini":
            missing.extend(_require(settings, ("gemini_api_key",)))
        else:
            missing.append("SUMMARY_PROVIDER")

    if not _is_missing(settings.stripe_billing_portal_return_url):
        try:
            parse_http_origin(settings.stripe_billing_portal_return_url)
        except ValueError:
            missing.append("STRIPE_BILLING_PORTAL_RETURN_URL")

    if missing:
        raise RuntimeError(
            f"Missing or invalid required production settings: {', '.join(missing)}"
        )
