from __future__ import annotations

from dataclasses import dataclass

from app.core.config import get_settings


class BillingSessionStateError(ValueError):
    pass


class BillingSessionProviderError(RuntimeError):
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
        price_standard: str | None = None,
        checkout_success_url: str | None = None,
        checkout_cancel_url: str | None = None,
    ) -> None:
        settings = get_settings()
        self._stripe_client = stripe_client
        self.secret_key = secret_key or settings.stripe_secret_key
        self.price_starter = price_starter or settings.stripe_price_starter
        self.price_standard = price_standard or settings.stripe_price_standard
        self.checkout_success_url = checkout_success_url or settings.stripe_checkout_success_url
        self.checkout_cancel_url = checkout_cancel_url or settings.stripe_checkout_cancel_url

    def create_checkout_session(
        self,
        *,
        user_id: str,
        customer_email: str,
        clerk_user_id: str,
        plan_tier: str,
    ) -> HostedSession:
        price_id = self._resolve_price_id(plan_tier)
        stripe = self._get_client()
        try:
            session = stripe.checkout.Session.create(
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
                    }
                },
            )
        except Exception as exc:
            raise BillingSessionProviderError("Failed to create Stripe checkout session") from exc

        return HostedSession(url=session.url)

    def create_portal_session(self, *, customer_id: str | None, return_url: str) -> HostedSession:
        if not customer_id:
            raise BillingSessionStateError("Stripe customer ID is required")

        stripe = self._get_client()
        try:
            session = stripe.billing_portal.Session.create(
                customer=customer_id,
                return_url=return_url,
            )
        except Exception as exc:
            raise BillingSessionProviderError("Failed to create Stripe billing portal session") from exc

        return HostedSession(url=session.url)

    def _resolve_price_id(self, plan_tier: str) -> str:
        if plan_tier == "starter":
            return self._require_config(self.price_starter, "Stripe starter price is required")
        if plan_tier == "standard":
            return self._require_config(self.price_standard, "Stripe standard price is required")
        raise BillingSessionStateError(f"Unsupported plan tier: {plan_tier}")

    def _get_client(self):
        if self._stripe_client is not None:
            return self._stripe_client

        if not self.secret_key:
            raise BillingSessionStateError("Stripe secret key is required")

        try:
            import stripe
        except ImportError as exc:
            raise BillingSessionProviderError("stripe is required for hosted billing sessions") from exc

        stripe.api_key = self.secret_key
        self._stripe_client = stripe
        return stripe

    @staticmethod
    def _require_config(value: str | None, message: str) -> str:
        if not value:
            raise BillingSessionStateError(message)
        return value
