from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import NoReturn

from app.core.config import get_settings
from app.core.http_origin import parse_http_origin
from app.core.observability import (
    get_observability,
    instrument_provider,
)
from app.core.provider_failures import ProviderFailure, ProviderOperation
from app.providers.subscriptions import stripe as stripe_provider


class BillingSessionStateError(ValueError):
    pass


class BillingPortalReturnUrlError(BillingSessionStateError):
    pass


@dataclass(frozen=True)
class HostedSession:
    url: str
    provider_session_id: str | None = None


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
        billing_portal_configuration_id: str | None = None,
        observability=None,
    ) -> None:
        settings = get_settings()
        self._stripe_client = stripe_client
        self.secret_key = secret_key or settings.stripe_secret_key
        self.price_starter = price_starter or settings.stripe_price_starter
        self.checkout_success_url = (
            checkout_success_url or settings.stripe_checkout_success_url
        )
        self.checkout_cancel_url = (
            checkout_cancel_url or settings.stripe_checkout_cancel_url
        )
        self.billing_portal_return_url = (
            billing_portal_return_url or settings.stripe_billing_portal_return_url
        )
        self.billing_portal_configuration_id = (
            billing_portal_configuration_id
            or settings.stripe_billing_portal_configuration_id
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
        lifecycle_generation: int,
        customer_id: str | None = None,
        idempotency_key: str | None = None,
        existing_session_id: str | None = None,
    ) -> HostedSession:
        price_id = self._resolve_price_id(plan_tier)
        metadata = {
            "clerk_user_id": clerk_user_id,
            "user_id": user_id,
            "plan_tier": plan_tier,
            "lifecycle_generation": str(lifecycle_generation),
        }
        stripe = await asyncio.to_thread(
            self._get_client,
            operation="create_checkout_session",
        )
        try:
            if existing_session_id is not None:
                session = await asyncio.to_thread(
                    stripe.checkout.Session.retrieve,
                    existing_session_id,
                )
            else:
                customer_argument = (
                    {"customer": customer_id}
                    if customer_id
                    else {"customer_email": customer_email}
                )
                idempotency_argument = (
                    {"idempotency_key": idempotency_key}
                    if idempotency_key
                    else {}
                )
                session = await asyncio.to_thread(
                    stripe.checkout.Session.create,
                    mode="subscription",
                    success_url=self._require_config(
                        self.checkout_success_url,
                        "Stripe checkout success URL is required",
                    ),
                    cancel_url=self._require_config(
                        self.checkout_cancel_url,
                        "Stripe checkout cancel URL is required",
                    ),
                    line_items=[{"price": price_id, "quantity": 1}],
                    metadata=metadata,
                    subscription_data={"metadata": metadata.copy()},
                    **customer_argument,
                    **idempotency_argument,
                )
        except Exception as exc:
            self._raise_known_stripe_failure(exc, operation="create_checkout_session")

        provider_session_id = self._required_response_string(
            session,
            field="id",
            operation="create_checkout_session",
        )
        url = self._required_response_string(
            session,
            field="url",
            operation="create_checkout_session",
        )
        return HostedSession(
            url=url,
            provider_session_id=provider_session_id,
        )

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
                raise BillingPortalReturnUrlError("Invalid billing portal return URL")

        stripe = await asyncio.to_thread(
            self._get_client,
            operation="create_portal_session",
        )
        try:
            session = await asyncio.to_thread(
                stripe.billing_portal.Session.create,
                customer=customer_id,
                return_url=configured_return_url,
                configuration=self._require_config(
                    self.billing_portal_configuration_id,
                    "Stripe billing portal configuration ID is required",
                ),
            )
        except Exception as exc:
            self._raise_known_stripe_failure(exc, operation="create_portal_session")

        return HostedSession(
            url=self._required_response_string(
                session,
                field="url",
                operation="create_portal_session",
            )
        )

    def _resolve_price_id(self, plan_tier: str) -> str:
        if plan_tier == "starter":
            return self._require_config(
                self.price_starter, "Stripe starter price is required"
            )
        raise BillingSessionStateError(f"Unsupported plan tier: {plan_tier}")

    def _get_client(
        self,
        *,
        operation: ProviderOperation = "create_checkout_session",
    ):
        if self._stripe_client is not None:
            return self._stripe_client

        if not self.secret_key:
            raise BillingSessionStateError("Stripe secret key is required")

        try:
            stripe_provider._install_safe_stripe_sdk_logging()
            import stripe
            from stripe._http_client import RequestsClient
        except ImportError as exc:
            raise ProviderFailure(
                provider="stripe",
                operation=operation,
                disposition="terminal",
                error_class="validation",
            ) from exc

        stripe.api_key = self.secret_key
        stripe.max_network_retries = 2
        if getattr(stripe.default_http_client, "_timeout", None) != (5, 30):
            stripe.default_http_client = RequestsClient(timeout=(5, 30))
        self._stripe_client = stripe
        return stripe

    @staticmethod
    def _raise_known_stripe_failure(
        error: Exception,
        *,
        operation: ProviderOperation,
    ) -> NoReturn:
        failure = stripe_provider.classify_stripe_exception(error, operation=operation)
        if failure is None:
            raise error
        raise failure from error

    @staticmethod
    def _required_response_string(
        response: object,
        *,
        field: str,
        operation: ProviderOperation,
    ) -> str:
        value = (
            response.get(field)
            if isinstance(response, dict)
            else getattr(response, field, None)
        )
        if isinstance(value, str) and value:
            return value
        raise ProviderFailure(
            provider="stripe",
            operation=operation,
            disposition="terminal",
            error_class="validation",
        )

    @staticmethod
    def _require_config(value: str | None, message: str) -> str:
        if not value:
            raise BillingSessionStateError(message)
        return value
