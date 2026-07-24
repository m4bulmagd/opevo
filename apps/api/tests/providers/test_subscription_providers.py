import asyncio
import logging
from types import SimpleNamespace

import pytest
import stripe

from app.core.config import Settings
from app.providers.subscriptions.base import SubscriptionProviderError
from app.providers.subscriptions.factory import build_subscription_provider
from app.providers.subscriptions.fake import FakeSubscriptionProvider
from app.providers.subscriptions.stripe import StripeSubscriptionProvider


class FakeSubscriptionAPI:
    def __init__(self, *, response: object | None = None, error: Exception | None = None) -> None:
        self.response = response or SimpleNamespace(id="sub_current", status="canceled")
        self.error = error
        self.calls: list[tuple[str, dict[str, object]]] = []

    def cancel(self, subscription_id: str, **kwargs: object) -> object:
        self.calls.append((subscription_id, kwargs))
        if self.error is not None:
            raise self.error
        return self.response


class FakeStripeClient:
    def __init__(self, *, response: object | None = None, error: Exception | None = None) -> None:
        self.Subscription = FakeSubscriptionAPI(response=response, error=error)


@pytest.mark.anyio
async def test_fake_provider_validates_subscription_identity_without_provider_io() -> None:
    provider = FakeSubscriptionProvider()

    await provider.cancel_immediately("sub_current")

    with pytest.raises(SubscriptionProviderError) as exc_info:
        await provider.cancel_immediately(" ")

    assert exc_info.value.category == "provider_terminal"
    assert exc_info.value.error_class == "validation"
    assert str(exc_info.value) == "validation"


def test_factory_uses_fake_provider_by_default(settings: Settings) -> None:
    provider = build_subscription_provider(settings)

    assert isinstance(provider, FakeSubscriptionProvider)


def test_factory_uses_stripe_provider_in_stripe_mode(settings: Settings) -> None:
    provider = build_subscription_provider(
        settings.model_copy(
            update={"billing_mode": "stripe", "stripe_secret_key": "sk_test_value"}
        )
    )

    assert isinstance(provider, StripeSubscriptionProvider)


@pytest.mark.anyio
async def test_stripe_cancels_exact_subscription_without_proration() -> None:
    client = FakeStripeClient()
    provider = StripeSubscriptionProvider(
        stripe_client=client,
        secret_key="sk_test_value",
    )

    await provider.cancel_immediately("sub_current")

    assert client.Subscription.calls == [
        (
            "sub_current",
            {
                "invoice_now": False,
                "prorate": False,
                "api_key": "sk_test_value",
            },
        )
    ]


@pytest.mark.anyio
async def test_stripe_cancellation_does_not_block_the_event_loop() -> None:
    class BlockingSubscriptionAPI(FakeSubscriptionAPI):
        def cancel(self, subscription_id: str, **kwargs: object) -> object:
            import time

            time.sleep(0.04)
            return super().cancel(subscription_id, **kwargs)

    client = FakeStripeClient()
    client.Subscription = BlockingSubscriptionAPI()
    provider = StripeSubscriptionProvider(stripe_client=client, secret_key="sk_test_value")
    heartbeat = asyncio.create_task(asyncio.sleep(0.01))

    await provider.cancel_immediately("sub_current")

    assert heartbeat.done()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("provider_error", "category", "error_class"),
    [
        (TimeoutError("timeout secret"), "provider_retryable", "timeout"),
        (
            stripe.error.APIConnectionError("connection secret"),
            "provider_retryable",
            "unavailable",
        ),
        (
            stripe.error.RateLimitError("rate secret"),
            "provider_retryable",
            "rate_limited",
        ),
        (
            stripe.error.APIError("429 secret", http_status=429),
            "provider_retryable",
            "rate_limited",
        ),
        (
            stripe.error.APIError("timeout secret", http_status=504),
            "provider_retryable",
            "timeout",
        ),
        (
            stripe.error.APIError("unavailable secret", http_status=503),
            "provider_retryable",
            "unavailable",
        ),
        (
            stripe.error.AuthenticationError("auth secret"),
            "provider_terminal",
            "authentication",
        ),
        (
            stripe.error.PermissionError("permission secret"),
            "provider_terminal",
            "authentication",
        ),
        (
            stripe.error.InvalidRequestError("validation secret", "subscription"),
            "provider_terminal",
            "validation",
        ),
        (
            stripe.error.APIError("conflict secret", http_status=409),
            "provider_terminal",
            "conflict",
        ),
        (
            stripe.error.APIError("validation secret", http_status=422),
            "provider_terminal",
            "validation",
        ),
    ],
)
async def test_stripe_errors_map_to_safe_contract_without_raw_messages(
    caplog: pytest.LogCaptureFixture,
    provider_error: Exception,
    category: str,
    error_class: str,
) -> None:
    provider = StripeSubscriptionProvider(
        stripe_client=FakeStripeClient(error=provider_error),
        secret_key="sk_test_value",
    )

    with caplog.at_level(logging.DEBUG), pytest.raises(SubscriptionProviderError) as exc_info:
        await provider.cancel_immediately("sub_current")

    assert exc_info.value.category == category
    assert exc_info.value.error_class == error_class
    assert str(exc_info.value) == error_class
    assert "secret" not in str(exc_info.value)
    assert "secret" not in caplog.text


@pytest.mark.anyio
async def test_missing_stored_subscription_is_idempotent_success() -> None:
    error = stripe.error.InvalidRequestError("missing subscription secret", "id")
    error.code = "resource_missing"
    error.http_status = 404
    provider = StripeSubscriptionProvider(
        stripe_client=FakeStripeClient(error=error),
        secret_key="sk_test_value",
    )

    await provider.cancel_immediately("sub_current")


@pytest.mark.anyio
@pytest.mark.parametrize(
    "response",
    [
        SimpleNamespace(id="sub_other", status="canceled"),
        SimpleNamespace(id="sub_current", status="active"),
        object(),
    ],
)
async def test_stripe_response_must_prove_requested_cancellation(response: object) -> None:
    provider = StripeSubscriptionProvider(
        stripe_client=FakeStripeClient(response=response),
        secret_key="sk_test_value",
    )

    with pytest.raises(SubscriptionProviderError) as exc_info:
        await provider.cancel_immediately("sub_current")

    assert exc_info.value.category == "provider_terminal"
    assert exc_info.value.error_class in {"conflict", "validation"}
