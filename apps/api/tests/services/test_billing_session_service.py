import asyncio
import time

import pytest
import stripe


class FakeCheckoutSessionAPI:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return type("CheckoutSession", (), {"url": "https://checkout.stripe.test/session"})()


class FakePortalSessionAPI:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return type("PortalSession", (), {"url": "https://billing.stripe.test/session"})()


class FakeStripeClient:
    def __init__(self) -> None:
        self.checkout = type("CheckoutNamespace", (), {"Session": FakeCheckoutSessionAPI()})()
        self.billing_portal = type("BillingPortalNamespace", (), {"Session": FakePortalSessionAPI()})()


class BlockingCheckoutSessionAPI(FakeCheckoutSessionAPI):
    def create(self, **kwargs):
        time.sleep(0.1)
        return super().create(**kwargs)


class BlockingStripeClient(FakeStripeClient):
    def __init__(self) -> None:
        super().__init__()
        self.checkout = type(
            "CheckoutNamespace",
            (),
            {"Session": BlockingCheckoutSessionAPI()},
        )()


class FailingCheckoutSessionAPI:
    error: Exception

    def create(self, **_kwargs):
        raise self.error


class FailingStripeClient(FakeStripeClient):
    def __init__(self, error: Exception) -> None:
        super().__init__()
        FailingCheckoutSessionAPI.error = error
        self.checkout = type(
            "CheckoutNamespace",
            (),
            {"Session": FailingCheckoutSessionAPI()},
        )()


@pytest.mark.anyio
async def test_create_checkout_session_uses_price_mapping() -> None:
    from app.services.billing_session_service import BillingSessionService

    client = FakeStripeClient()
    service = BillingSessionService(
        stripe_client=client,
        secret_key="sk_test_123",
        price_starter="price_starter_123",
        checkout_success_url="https://app.example.com/success",
        checkout_cancel_url="https://app.example.com/cancel",
        billing_portal_return_url="https://app.example.com/dashboard/billing",
    )

    result = await service.create_checkout_session(
        user_id="user_123",
        customer_email="billing@example.com",
        clerk_user_id="clerk_123",
        plan_tier="starter",
    )

    assert result.url == "https://checkout.stripe.test/session"
    assert client.checkout.Session.calls[0]["line_items"][0]["price"] == "price_starter_123"
    assert client.checkout.Session.calls[0]["metadata"]["plan_tier"] == "starter"
    assert client.checkout.Session.calls[0]["metadata"]["clerk_user_id"] == "clerk_123"
    assert client.checkout.Session.calls[0]["subscription_data"]["metadata"]["plan_tier"] == "starter"


@pytest.mark.anyio
async def test_create_checkout_session_rejects_standard_plan() -> None:
    from app.services.billing_session_service import BillingSessionService

    service = BillingSessionService(
        stripe_client=FakeStripeClient(),
        secret_key="sk_test_123",
        price_starter="price_starter_123",
        checkout_success_url="https://app.example.com/success",
        checkout_cancel_url="https://app.example.com/cancel",
        billing_portal_return_url="https://app.example.com/dashboard/billing",
    )

    with pytest.raises(ValueError, match="Unsupported plan tier: standard"):
        await service.create_checkout_session(
            user_id="user_123",
            customer_email="billing@example.com",
            clerk_user_id="clerk_123",
            plan_tier="standard",
        )


@pytest.mark.anyio
async def test_create_portal_session_requires_customer_id() -> None:
    from app.services.billing_session_service import BillingSessionService

    service = BillingSessionService(
        stripe_client=FakeStripeClient(),
        secret_key="sk_test_123",
        price_starter="price_starter_123",
        checkout_success_url="https://app.example.com/success",
        checkout_cancel_url="https://app.example.com/cancel",
        billing_portal_return_url="https://app.example.com/dashboard/billing",
    )

    with pytest.raises(ValueError, match="Stripe customer ID is required"):
        await service.create_portal_session(customer_id=None, return_url="https://app.example.com/settings")


@pytest.mark.anyio
async def test_create_portal_session_always_uses_server_owned_return_url() -> None:
    from app.services.billing_session_service import BillingSessionService

    client = FakeStripeClient()
    service = BillingSessionService(
        stripe_client=client,
        secret_key="sk_test_123",
        price_starter="price_starter_123",
        checkout_success_url="https://app.example.com/success",
        checkout_cancel_url="https://app.example.com/cancel",
        billing_portal_return_url="https://app.example.com/dashboard/billing",
    )

    result = await service.create_portal_session(
        customer_id="cus_123",
        return_url="https://app.example.com/account?tab=billing",
    )

    assert result.url == "https://billing.stripe.test/session"
    assert client.billing_portal.Session.calls == [
        {
            "customer": "cus_123",
            "return_url": "https://app.example.com/dashboard/billing",
        }
    ]


@pytest.mark.anyio
async def test_create_portal_session_accepts_omitted_caller_return_url() -> None:
    from app.services.billing_session_service import BillingSessionService

    client = FakeStripeClient()
    service = BillingSessionService(
        stripe_client=client,
        billing_portal_return_url="https://app.example.com/dashboard/billing",
    )

    await service.create_portal_session(customer_id="cus_123", return_url=None)

    assert client.billing_portal.Session.calls[0]["return_url"] == (
        "https://app.example.com/dashboard/billing"
    )


@pytest.mark.parametrize(
    "caller_return_url",
    [
        "http://app.example.com/settings",
        "https://evil.example.com/settings",
        "https://app.example.com:444/settings",
        "https://user@app.example.com/settings",
        "https://app.example.com:bad/settings",
        "https://app.example.com/has space",
        "ftp://app.example.com/settings",
        "/dashboard/billing",
        "not a url",
    ],
)
@pytest.mark.anyio
async def test_create_portal_session_rejects_unsafe_caller_return_url(
    caller_return_url: str,
) -> None:
    from app.services.billing_session_service import (
        BillingPortalReturnUrlError,
        BillingSessionService,
    )

    service = BillingSessionService(
        stripe_client=FakeStripeClient(),
        billing_portal_return_url="https://app.example.com/dashboard/billing",
    )

    with pytest.raises(BillingPortalReturnUrlError):
        await service.create_portal_session(
            customer_id="cus_123",
            return_url=caller_return_url,
        )


@pytest.mark.anyio
async def test_stripe_call_does_not_block_the_event_loop() -> None:
    service = __import__(
        "app.services.billing_session_service",
        fromlist=["BillingSessionService"],
    ).BillingSessionService(
        stripe_client=BlockingStripeClient(),
        price_starter="price_starter_123",
        checkout_success_url="https://app.example.com/success",
        checkout_cancel_url="https://app.example.com/cancel",
    )
    heartbeat = asyncio.create_task(asyncio.sleep(0.02))

    await service.create_checkout_session(
        user_id="user_123",
        customer_email="billing@example.com",
        clerk_user_id="clerk_123",
        plan_tier="starter",
    )

    assert heartbeat.done()
    await heartbeat


def test_stripe_sdk_uses_bounded_network_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.billing_session_service import BillingSessionService

    monkeypatch.setattr(stripe, "default_http_client", None)
    monkeypatch.setattr(stripe, "max_network_retries", 0)
    service = BillingSessionService(secret_key="sk_test_123")

    client = service._get_client()

    assert client.max_network_retries == 2
    assert client.default_http_client._timeout == (5, 30)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("provider_error", "expected_category"),
    [
        (
            stripe.error.APIConnectionError(
                "retryable connection secret",
                should_retry=True,
            ),
            "provider_retryable",
        ),
        (stripe.error.RateLimitError("rate limit secret"), "provider_retryable"),
        (
            stripe.error.APIError("server secret", http_status=503),
            "provider_retryable",
        ),
        (stripe.error.AuthenticationError("auth secret"), "provider_terminal"),
        (stripe.error.PermissionError("permission secret"), "provider_terminal"),
        (
            stripe.error.InvalidRequestError("validation secret", "customer"),
            "provider_terminal",
        ),
        (
            stripe.error.APIError("client secret", http_status=404),
            "provider_terminal",
        ),
    ],
)
async def test_stripe_errors_use_safe_fixed_categories(
    provider_error: Exception,
    expected_category: str,
) -> None:
    from app.services.billing_session_service import (
        BillingSessionProviderError,
        BillingSessionService,
    )

    service = BillingSessionService(
        stripe_client=FailingStripeClient(provider_error),
        price_starter="price_starter_123",
        checkout_success_url="https://app.example.com/success",
        checkout_cancel_url="https://app.example.com/cancel",
    )

    with pytest.raises(BillingSessionProviderError) as exc_info:
        await service.create_checkout_session(
            user_id="user_123",
            customer_email="billing@example.com",
            clerk_user_id="clerk_123",
            plan_tier="starter",
        )

    assert exc_info.value.category == expected_category
    assert str(exc_info.value) == expected_category
