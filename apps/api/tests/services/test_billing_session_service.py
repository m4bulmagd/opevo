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
        price_standard="price_standard_123",
        checkout_success_url="https://app.example.com/success",
        checkout_cancel_url="https://app.example.com/cancel",
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
        price_standard="price_standard_123",
        checkout_success_url="https://app.example.com/success",
        checkout_cancel_url="https://app.example.com/cancel",
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
        price_standard="price_standard_123",
        checkout_success_url="https://app.example.com/success",
        checkout_cancel_url="https://app.example.com/cancel",
    )

    with pytest.raises(ValueError, match="Stripe customer ID is required"):
        service.create_portal_session(customer_id=None, return_url="https://app.example.com/settings")
