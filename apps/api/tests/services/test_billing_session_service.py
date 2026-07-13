import pytest


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


def test_create_checkout_session_uses_price_mapping() -> None:
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

    result = service.create_checkout_session(
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


def test_create_checkout_session_rejects_standard_plan() -> None:
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
        service.create_checkout_session(
            user_id="user_123",
            customer_email="billing@example.com",
            clerk_user_id="clerk_123",
            plan_tier="standard",
        )


def test_create_portal_session_requires_customer_id() -> None:
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
        service.create_portal_session(customer_id=None, return_url="https://app.example.com/settings")


def test_create_portal_session_always_uses_server_owned_return_url() -> None:
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

    result = service.create_portal_session(
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


def test_create_portal_session_accepts_omitted_caller_return_url() -> None:
    from app.services.billing_session_service import BillingSessionService

    client = FakeStripeClient()
    service = BillingSessionService(
        stripe_client=client,
        billing_portal_return_url="https://app.example.com/dashboard/billing",
    )

    service.create_portal_session(customer_id="cus_123", return_url=None)

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
def test_create_portal_session_rejects_unsafe_caller_return_url(
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
        service.create_portal_session(
            customer_id="cus_123",
            return_url=caller_return_url,
        )
