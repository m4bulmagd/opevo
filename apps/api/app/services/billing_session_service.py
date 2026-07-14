from __future__ import annotations

import asyncio
from dataclasses import dataclass

from app.core.config import get_settings
from app.core.http_origin import parse_http_origin
from app.core.observability import (
    get_observability,
    instrument_provider,
    validated_error_class,
)


class BillingSessionStateError(ValueError):
    pass


class BillingSessionProviderError(RuntimeError):
    def __init__(
        self,
        category: str,
        *,
        error_class: str | None = None,
    ) -> None:
        if category not in {"provider_retryable", "provider_terminal"}:
            raise ValueError("Unsafe billing provider category")
        super().__init__(category)
        self.category = category
        self.retryable = category == "provider_retryable"
        self.error_class = validated_error_class(
            error_class or ("unavailable" if self.retryable else "unknown")
        )


class BillingPortalReturnUrlError(BillingSessionStateError):
    pass


@dataclass(frozen=True)
class HostedSession:
    url: str


class BillingSessionService:
    def __init__(
        self,
        *,
        stripe_client=None,
        secret_key: str | None = None,
        price_starter: str | None = None,
        checkout_success_url: str | None = None,
        checkout_cancel_url: str | None = None,
        billing_portal_return_url: str | None = None,
        observability=None,
    ) -> None:
        settings = get_settings()
        self._stripe_client = stripe_client
        self.secret_key = secret_key or settings.stripe_secret_key
        self.price_starter = price_starter or settings.stripe_price_starter
        self.checkout_success_url = checkout_success_url or settings.stripe_checkout_success_url
        self.checkout_cancel_url = checkout_cancel_url or settings.stripe_checkout_cancel_url
        self.billing_portal_return_url = (
            billing_portal_return_url or settings.stripe_billing_portal_return_url
        )
        self.observability = observability or get_observability()

    @instrument_provider("stripe", "create_checkout_session")
    async def create_checkout_session(
        self,
        *,
        user_id: str,
        customer_email: str,
        clerk_user_id: str,
        plan_tier: str,
    ) -> HostedSession:
        price_id = self._resolve_price_id(plan_tier)
        stripe = await asyncio.to_thread(self._get_client)
        try:
            session = await asyncio.to_thread(
                stripe.checkout.Session.create,
                mode="subscription",
                customer_email=customer_email,
                success_url=self._require_config(self.checkout_success_url, "Stripe checkout success URL is required"),
                cancel_url=self._require_config(self.checkout_cancel_url, "Stripe checkout cancel URL is required"),
                line_items=[{"price": price_id, "quantity": 1}],
                metadata={
                    "user_id": user_id,
                    "clerk_user_id": clerk_user_id,
                    "plan_tier": plan_tier,
                },
                subscription_data={
                    "metadata": {
                        "user_id": user_id,
                        "clerk_user_id": clerk_user_id,
                        "plan_tier": plan_tier,
                    },
                },
            )
        except Exception as exc:
            category, error_class = self._stripe_error_details(exc)
            raise BillingSessionProviderError(
                category,
                error_class=error_class,
            ) from None

        return HostedSession(url=session.url)

    @instrument_provider("stripe", "create_portal_session")
    async def create_portal_session(
        self,
        *,
        customer_id: str | None,
        return_url: str | None = None,
    ) -> HostedSession:
        if not customer_id:
            raise BillingSessionStateError("Stripe customer ID is required")

        configured_return_url = self._require_config(
            self.billing_portal_return_url,
            "Stripe billing portal return URL is required",
        )
        try:
            configured_origin = parse_http_origin(configured_return_url)
        except ValueError:
            raise BillingSessionStateError(
                "Stripe billing portal return URL is invalid"
            ) from None

        if return_url is not None:
            try:
                caller_origin = parse_http_origin(return_url)
            except ValueError:
                raise BillingPortalReturnUrlError(
                    "Invalid billing portal return URL"
                ) from None
            if caller_origin != configured_origin:
                raise BillingPortalReturnUrlError(
                    "Invalid billing portal return URL"
                )

        stripe = await asyncio.to_thread(self._get_client)
        try:
            session = await asyncio.to_thread(
                stripe.billing_portal.Session.create,
                customer=customer_id,
                return_url=configured_return_url,
            )
        except Exception as exc:
            category, error_class = self._stripe_error_details(exc)
            raise BillingSessionProviderError(
                category,
                error_class=error_class,
            ) from None

        return HostedSession(url=session.url)

    def _resolve_price_id(self, plan_tier: str) -> str:
        if plan_tier == "starter":
            return self._require_config(self.price_starter, "Stripe starter price is required")
        raise BillingSessionStateError(f"Unsupported plan tier: {plan_tier}")

    def _get_client(self):
        if self._stripe_client is not None:
            return self._stripe_client

        if not self.secret_key:
            raise BillingSessionStateError("Stripe secret key is required")

        try:
            import stripe
            from stripe._http_client import RequestsClient
        except ImportError:
            raise BillingSessionProviderError(
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
    def _stripe_error_details(error: Exception) -> tuple[str, str]:
        import stripe

        if isinstance(error, stripe.error.APIConnectionError):
            category = (
                "provider_retryable"
                if getattr(error, "should_retry", False) is True
                else "provider_terminal"
            )
            return category, "unavailable"
        if isinstance(error, stripe.error.RateLimitError):
            return "provider_retryable", "rate_limited"
        if isinstance(
            error,
            (
                stripe.error.AuthenticationError,
                stripe.error.PermissionError,
            ),
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

    @staticmethod
    def _require_config(value: str | None, message: str) -> str:
        if not value:
            raise BillingSessionStateError(message)
        return value
