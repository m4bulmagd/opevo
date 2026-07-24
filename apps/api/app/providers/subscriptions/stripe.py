import asyncio
import logging
from typing import Any

from app.core.config import get_settings
from app.providers.subscriptions.base import (
    ProviderErrorCategory,
    ProviderErrorClass,
    SubscriptionProvider,
    SubscriptionProviderError,
)


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


def _install_safe_stripe_sdk_logging() -> None:
    import stripe
    from stripe import _util as stripe_util

    # Stripe's console mode prints before logging filters run. Keep SDK
    # diagnostics in the standard logger, where provider details are filtered.
    stripe.log = None
    stripe_util.STRIPE_LOG = None
    stripe_logger = logging.getLogger("stripe")
    if _SAFE_STRIPE_SDK_LOG_FILTER not in stripe_logger.filters:
        stripe_logger.addFilter(_SAFE_STRIPE_SDK_LOG_FILTER)


class StripeSubscriptionProvider(SubscriptionProvider):
    def __init__(
        self,
        *,
        stripe_client: object | None = None,
        secret_key: str | None = None,
    ) -> None:
        self._stripe_client = stripe_client
        self.secret_key = secret_key or get_settings().stripe_secret_key

    async def cancel_immediately(self, subscription_id: str) -> None:
        if not isinstance(subscription_id, str) or not subscription_id.strip():
            raise SubscriptionProviderError(
                "provider_terminal",
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
            category, error_class = self._stripe_error_details(error)
            raise SubscriptionProviderError(category, error_class=error_class) from None

        response_id = self._read(response, "id")
        if response_id != subscription_id:
            raise SubscriptionProviderError(
                "provider_terminal",
                error_class="conflict",
            )
        if self._read(response, "status") != "canceled":
            raise SubscriptionProviderError(
                "provider_terminal",
                error_class="validation",
            )

    def _get_client(self) -> Any:
        _install_safe_stripe_sdk_logging()
        if self._stripe_client is not None:
            return self._stripe_client
        if not self.secret_key:
            raise SubscriptionProviderError(
                "provider_terminal",
                error_class="validation",
            )

        try:
            import stripe
            from stripe._http_client import RequestsClient
        except ImportError:
            raise SubscriptionProviderError(
                "provider_terminal",
                error_class="validation",
            ) from None

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
        return (
            cls._read(error, "code") == "resource_missing"
            and cls._read(error, "http_status") == 404
        )

    @staticmethod
    def _stripe_error_details(
        error: Exception,
    ) -> tuple[ProviderErrorCategory, ProviderErrorClass]:
        import stripe

        if isinstance(error, TimeoutError):
            return "provider_retryable", "timeout"
        if isinstance(error, stripe.error.APIConnectionError):
            return "provider_retryable", "unavailable"
        if isinstance(error, stripe.error.RateLimitError):
            return "provider_retryable", "rate_limited"
        if isinstance(
            error,
            (stripe.error.AuthenticationError, stripe.error.PermissionError),
        ):
            return "provider_terminal", "authentication"
        if isinstance(error, stripe.error.InvalidRequestError):
            return "provider_terminal", "validation"
        if isinstance(error, stripe.error.APIError):
            status = error.http_status
            if status == 429:
                return "provider_retryable", "rate_limited"
            if status in {408, 504}:
                return "provider_retryable", "timeout"
            if status is not None and status >= 500:
                return "provider_retryable", "unavailable"
            if status in {401, 403}:
                return "provider_terminal", "authentication"
            if status == 409:
                return "provider_terminal", "conflict"
            if status in {400, 404, 405, 422}:
                return "provider_terminal", "validation"
            return "provider_terminal", "unknown"
        if isinstance(error, stripe.error.StripeError):
            return "provider_terminal", "unknown"
        return "provider_retryable", "unknown"
