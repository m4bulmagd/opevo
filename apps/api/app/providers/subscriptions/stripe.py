import asyncio
import logging
from typing import Any

from app.core.provider_failures import (
    ProviderFailure,
    ProviderOperation,
    provider_failure_from_http_status,
)
from app.providers.subscriptions.base import SubscriptionProvider


_UNSAFE_STRIPE_SDK_LOG_MARKERS = (
    "message='Stripe v1 API error received'",
    "message='Stripe v2 API error received'",
    "message='API response body'",
)


class _SafeStripeSdkLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if (
            record.name == "stripe"
            and isinstance(record.msg, str)
            and any(marker in record.msg for marker in _UNSAFE_STRIPE_SDK_LOG_MARKERS)
        ):
            record.msg = "message='Stripe API provider details suppressed'"
            record.args = ()
            record.exc_info = None
            record.exc_text = None
            record.stack_info = None
        return True


_SAFE_STRIPE_SDK_LOG_FILTER = _SafeStripeSdkLogFilter()


def install_safe_stripe_log_filter() -> None:
    """Install bounded Stripe logger redaction without importing the SDK."""
    stripe_logger = logging.getLogger("stripe")
    if _SAFE_STRIPE_SDK_LOG_FILTER not in stripe_logger.filters:
        stripe_logger.addFilter(_SAFE_STRIPE_SDK_LOG_FILTER)


def configure_safe_stripe_sdk_logging(stripe: Any) -> None:
    """Disable Stripe SDK console diagnostics after the SDK is available."""
    from stripe import _util as stripe_util

    # Stripe's console mode prints before logging filters run. Keep SDK
    # diagnostics in the standard logger, where provider details are filtered.
    stripe.log = None
    stripe_util.STRIPE_LOG = None


def classify_stripe_exception(
    error: Exception,
    *,
    operation: ProviderOperation,
) -> ProviderFailure | None:
    """Translate only known Stripe or transport failures into safe vocabulary."""
    if isinstance(error, TimeoutError):
        return ProviderFailure(
            provider="stripe",
            operation=operation,
            disposition="retryable",
            error_class="timeout",
        )
    try:
        import stripe
    except ImportError:
        return None
    if isinstance(error, stripe.error.APIConnectionError):
        return ProviderFailure(
            provider="stripe",
            operation=operation,
            disposition=(
                "retryable" if getattr(error, "should_retry", False) is True else "terminal"
            ),
            error_class="unavailable",
        )
    if isinstance(error, stripe.error.RateLimitError):
        return ProviderFailure(
            provider="stripe",
            operation=operation,
            disposition="retryable",
            error_class="rate_limited",
        )
    if isinstance(error, (stripe.error.AuthenticationError, stripe.error.PermissionError)):
        return ProviderFailure(
            provider="stripe",
            operation=operation,
            disposition="terminal",
            error_class="authentication",
        )
    if isinstance(error, stripe.error.InvalidRequestError):
        return ProviderFailure(
            provider="stripe",
            operation=operation,
            disposition="terminal",
            error_class="validation",
        )
    if isinstance(error, stripe.error.APIError):
        if error.http_status == 504:
            return ProviderFailure(
                provider="stripe",
                operation=operation,
                disposition="retryable",
                error_class="timeout",
            )
        return provider_failure_from_http_status(
            provider="stripe",
            operation=operation,
            status=error.http_status,
        )
    if isinstance(error, stripe.error.StripeError):
        return ProviderFailure(
            provider="stripe",
            operation=operation,
            disposition="terminal",
            error_class="unknown",
        )
    return None


class StripeSubscriptionProvider(SubscriptionProvider):
    def __init__(
        self,
        *,
        secret_key: str | None,
        stripe_client: object | None = None,
    ) -> None:
        self._stripe_client = stripe_client
        self.secret_key = secret_key

    async def cancel_immediately(self, subscription_id: str) -> None:
        if not isinstance(subscription_id, str) or not subscription_id.strip():
            raise ProviderFailure(
                provider="stripe",
                operation="cancel_subscription",
                disposition="terminal",
                error_class="validation",
            )

        stripe_client = await asyncio.to_thread(self._get_client)
        try:
            response = await asyncio.to_thread(
                stripe_client.Subscription.cancel,
                subscription_id,
                invoice_now=False,
                prorate=False,
                api_key=self.secret_key,
            )
        except Exception as error:
            if self._is_missing_subscription(error):
                return
            failure = classify_stripe_exception(
                error,
                operation="cancel_subscription",
            )
            if failure is None:
                raise
            raise failure from error

        response_id = self._read(response, "id")
        if response_id != subscription_id:
            raise ProviderFailure(
                provider="stripe",
                operation="cancel_subscription",
                disposition="terminal",
                error_class="conflict",
            )
        if self._read(response, "status") != "canceled":
            raise ProviderFailure(
                provider="stripe",
                operation="cancel_subscription",
                disposition="terminal",
                error_class="validation",
            )

    def _get_client(self) -> Any:
        install_safe_stripe_log_filter()
        if self._stripe_client is not None:
            return self._stripe_client
        if not self.secret_key:
            raise ProviderFailure(
                provider="stripe",
                operation="cancel_subscription",
                disposition="terminal",
                error_class="validation",
            )

        try:
            import stripe
            from stripe._http_client import RequestsClient
            configure_safe_stripe_sdk_logging(stripe)
        except ImportError as error:
            raise ProviderFailure(
                provider="stripe",
                operation="cancel_subscription",
                disposition="terminal",
                error_class="validation",
            ) from error

        stripe.api_key = self.secret_key
        stripe.max_network_retries = 2
        if getattr(stripe.default_http_client, "_timeout", None) != (5, 30):
            stripe.default_http_client = RequestsClient(timeout=(5, 30))
        self._stripe_client = stripe
        return stripe

    @staticmethod
    def _read(value: object, field: str) -> object | None:
        if isinstance(value, dict):
            return value.get(field)
        return getattr(value, field, None)

    @classmethod
    def _is_missing_subscription(cls, error: Exception) -> bool:
        try:
            import stripe
        except ImportError:
            return False

        return (
            isinstance(error, stripe.error.StripeError)
            and cls._read(error, "code") == "resource_missing"
            and cls._read(error, "http_status") == 404
        )
