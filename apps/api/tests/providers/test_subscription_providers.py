import asyncio
import builtins
import logging
from types import SimpleNamespace

import pytest
import stripe

from app.core.config import Settings
from app.core.provider_failures import ProviderFailure
from app.providers.subscriptions.factory import build_subscription_provider
from app.providers.subscriptions.fake import FakeSubscriptionProvider
from app.providers.subscriptions import stripe as stripe_provider
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


class StripeErrorResponseHTTPClient(stripe.HTTPClient):
    name = "provider-free-stripe-test"
    _timeout = (5, 30)

    def request(self, method, url, headers, post_data=None, *, _usage=None):
        return (
            (
                '{"error":{"type":"invalid_request_error",'
                '"code":"invalid_subscription",'
                '"message":"RAW_STRIPE_ERROR_SENTINEL",'
                '"param":"id"}}'
            ),
            400,
            {},
        )


def _deny_stripe_import(monkeypatch: pytest.MonkeyPatch) -> None:
    original_import = builtins.__import__

    def unavailable_stripe_import(name: str, *args, **kwargs):
        if name == "stripe" or name.startswith("stripe."):
            raise ImportError("STRIPE_SDK_UNAVAILABLE")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", unavailable_stripe_import)


@pytest.mark.anyio
async def test_fake_provider_validates_subscription_identity_without_provider_io() -> None:
    provider = FakeSubscriptionProvider()

    await provider.cancel_immediately("sub_current")

    with pytest.raises(ProviderFailure) as exc_info:
        await provider.cancel_immediately(" ")

    assert exc_info.value.provider == "fake"
    assert exc_info.value.operation == "validate"
    assert exc_info.value.disposition == "terminal"
    assert exc_info.value.error_class == "validation"
    assert str(exc_info.value) == "provider operation failed"


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
async def test_injected_subscription_client_works_without_stripe_sdk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _deny_stripe_import(monkeypatch)
    provider = StripeSubscriptionProvider(
        stripe_client=FakeStripeClient(),
        secret_key="sk_test_value",
    )

    await provider.cancel_immediately("sub_current")


@pytest.mark.anyio
@pytest.mark.parametrize("defect", [TypeError("TYPE_SENTINEL"), RuntimeError("RUNTIME_SENTINEL")])
async def test_injected_subscription_defect_propagates_without_stripe_sdk(
    defect: Exception,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _deny_stripe_import(monkeypatch)
    provider = StripeSubscriptionProvider(
        stripe_client=FakeStripeClient(error=defect),
        secret_key="sk_test_value",
    )

    with pytest.raises(type(defect)) as exc_info:
        await provider.cancel_immediately("sub_current")

    assert exc_info.value is defect


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
    ("provider_error", "disposition", "error_class"),
    [
        (TimeoutError("timeout secret"), "retryable", "timeout"),
        (
            stripe.error.APIConnectionError("connection retry secret", should_retry=True),
            "retryable",
            "unavailable",
        ),
        (
            stripe.error.APIConnectionError("connection terminal secret", should_retry=False),
            "terminal",
            "unavailable",
        ),
        (
            stripe.error.RateLimitError("rate secret"),
            "retryable",
            "rate_limited",
        ),
        (
            stripe.error.APIError("429 secret", http_status=429),
            "retryable",
            "rate_limited",
        ),
        (
            stripe.error.APIError("request timeout secret", http_status=408),
            "retryable",
            "timeout",
        ),
        (
            stripe.error.APIError("timeout secret", http_status=504),
            "retryable",
            "timeout",
        ),
        (
            stripe.error.APIError("unavailable secret", http_status=503),
            "retryable",
            "unavailable",
        ),
        (
            stripe.error.AuthenticationError("auth secret"),
            "terminal",
            "authentication",
        ),
        (
            stripe.error.PermissionError("permission secret"),
            "terminal",
            "authentication",
        ),
        (
            stripe.error.APIError("forbidden secret", http_status=403),
            "terminal",
            "authentication",
        ),
        (
            stripe.error.InvalidRequestError("validation secret", "subscription"),
            "terminal",
            "validation",
        ),
        (
            stripe.error.APIError("conflict secret", http_status=409),
            "terminal",
            "conflict",
        ),
        (
            stripe.error.APIError("not found secret", http_status=404),
            "terminal",
            "not_found",
        ),
        (
            stripe.error.APIError("validation secret", http_status=422),
            "terminal",
            "validation",
        ),
        (stripe.error.StripeError("base secret"), "terminal", "unknown"),
    ],
)
async def test_stripe_errors_map_to_safe_contract_without_raw_messages(
    caplog: pytest.LogCaptureFixture,
    provider_error: Exception,
    disposition: str,
    error_class: str,
) -> None:
    provider = StripeSubscriptionProvider(
        stripe_client=FakeStripeClient(error=provider_error),
        secret_key="sk_test_value",
    )

    with caplog.at_level(logging.DEBUG), pytest.raises(ProviderFailure) as exc_info:
        await provider.cancel_immediately("sub_current")

    assert exc_info.value.provider == "stripe"
    assert exc_info.value.operation == "cancel_subscription"
    assert exc_info.value.disposition == disposition
    assert exc_info.value.error_class == error_class
    assert exc_info.value.__cause__ is provider_error
    assert "secret" not in str(exc_info.value)
    assert "secret" not in repr(exc_info.value)
    assert "secret" not in exc_info.value.args[0]
    assert "secret" not in caplog.text


@pytest.mark.anyio
async def test_stripe_sdk_error_parsing_cannot_log_raw_provider_message(
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(stripe, "default_http_client", StripeErrorResponseHTTPClient())
    monkeypatch.setattr(stripe, "log", "debug")
    provider = StripeSubscriptionProvider(secret_key="sk_test_value")

    with caplog.at_level(logging.DEBUG), pytest.raises(ProviderFailure):
        logging.getLogger("app.unrelated").info(
            "unrelated application log remains visible"
        )
        await provider.cancel_immediately("sub_current")

    captured = capsys.readouterr()
    assert "RAW_STRIPE_ERROR_SENTINEL" not in caplog.text
    assert "RAW_STRIPE_ERROR_SENTINEL" not in captured.err
    assert "unrelated application log remains visible" in caplog.text
    assert "Request to Stripe api" in caplog.text


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

    with pytest.raises(ProviderFailure) as exc_info:
        await provider.cancel_immediately("sub_current")

    assert exc_info.value.disposition == "terminal"
    assert exc_info.value.error_class in {"conflict", "validation"}


@pytest.mark.anyio
@pytest.mark.parametrize("defect", [TypeError("TYPE_SENTINEL"), RuntimeError("RUNTIME_SENTINEL")])
async def test_arbitrary_injected_defects_propagate_unchanged(defect: Exception) -> None:
    provider = StripeSubscriptionProvider(
        stripe_client=FakeStripeClient(error=defect),
        secret_key="sk_test_value",
    )

    with pytest.raises(type(defect)) as exc_info:
        await provider.cancel_immediately("sub_current")

    assert exc_info.value is defect


@pytest.mark.anyio
async def test_cancellation_propagates_unchanged() -> None:
    provider = StripeSubscriptionProvider(
        stripe_client=FakeStripeClient(error=asyncio.CancelledError()),
        secret_key="sk_test_value",
    )

    with pytest.raises(asyncio.CancelledError):
        await provider.cancel_immediately("sub_current")


@pytest.mark.anyio
async def test_subscription_and_hosted_sessions_reuse_the_stripe_classifier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.billing_session_service import BillingSessionService

    calls: list[str] = []

    def controlled_classifier(error: Exception, *, operation: str) -> ProviderFailure:
        assert isinstance(error, stripe.error.StripeError)
        calls.append(operation)
        return ProviderFailure(
            provider="stripe",
            operation=operation,  # type: ignore[arg-type]
            disposition="terminal",
            error_class="unknown",
        )

    monkeypatch.setattr(stripe_provider, "classify_stripe_exception", controlled_classifier)
    error = stripe.error.StripeError("provider response sentinel")
    failing_checkout_api = type(
        "FailingCheckoutAPI",
        (),
        {"create": lambda self, **_kwargs: (_ for _ in ()).throw(error)},
    )()
    hosted_client = type(
        "HostedStripeClient",
        (),
        {
            "checkout": type(
                "CheckoutNamespace",
                (),
                {"Session": failing_checkout_api},
            )()
        },
    )()
    provider = StripeSubscriptionProvider(
        stripe_client=FakeStripeClient(error=error),
        secret_key="sk_test_value",
    )
    service = BillingSessionService(
        stripe_client=hosted_client,
        price_starter="price_starter_123",
        checkout_success_url="https://app.example.com/success",
        checkout_cancel_url="https://app.example.com/cancel",
    )

    with pytest.raises(ProviderFailure):
        await provider.cancel_immediately("sub_current")
    with pytest.raises(ProviderFailure):
        await service.create_checkout_session(
            user_id="user_123",
            customer_email="billing@example.com",
            clerk_user_id="clerk_123",
            plan_tier="starter",
            lifecycle_generation=7,
        )

    assert calls == ["cancel_subscription", "create_checkout_session"]
