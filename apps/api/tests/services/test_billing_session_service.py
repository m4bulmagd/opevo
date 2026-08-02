import asyncio
import time
from contextlib import asynccontextmanager

import pytest
import stripe

from app.core.provider_failures import ProviderFailure


class _Telemetry:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    @asynccontextmanager
    async def provider_operation(self, provider: str, operation: str, **_kwargs):
        try:
            yield
        except Exception:
            self.calls.append((provider, operation, "error"))
            raise
        else:
            self.calls.append((provider, operation, "success"))


class FakeCheckoutSessionAPI:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return type(
            "CheckoutSession",
            (),
            {
                "id": "cs_checkout_123",
                "url": "https://checkout.stripe.test/session",
            },
        )()


class FakePortalSessionAPI:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return type(
            "PortalSession", (), {"url": "https://billing.stripe.test/session"}
        )()


class FakeStripeClient:
    def __init__(self) -> None:
        self.checkout = type(
            "CheckoutNamespace", (), {"Session": FakeCheckoutSessionAPI()}
        )()
        self.billing_portal = type(
            "BillingPortalNamespace", (), {"Session": FakePortalSessionAPI()}
        )()


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
    def __init__(self, error: Exception, *, caller: str = "checkout") -> None:
        super().__init__()
        FailingCheckoutSessionAPI.error = error
        if caller == "checkout":
            self.checkout = type(
                "CheckoutNamespace",
                (),
                {"Session": FailingCheckoutSessionAPI()},
            )()
        else:
            self.billing_portal = type(
                "BillingPortalNamespace",
                (),
                {"Session": FailingCheckoutSessionAPI()},
            )()


@pytest.mark.anyio
async def test_create_checkout_session_uses_price_mapping() -> None:
    from app.services.billing_session_service import BillingSessionService

    client = FakeStripeClient()
    telemetry = _Telemetry()
    service = BillingSessionService(
        stripe_client=client,
        secret_key="sk_test_123",
        price_starter="price_starter_123",
        checkout_success_url="https://app.example.com/success",
        checkout_cancel_url="https://app.example.com/cancel",
        billing_portal_return_url="https://app.example.com/dashboard/billing",
        billing_portal_configuration_id="bpc_period_end_cancel",
        observability=telemetry,
    )

    result = await service.create_checkout_session(
        user_id="user_123",
        customer_email="billing@example.com",
        clerk_user_id="clerk_123",
        plan_tier="starter",
        lifecycle_generation=7,
    )

    assert result.url == "https://checkout.stripe.test/session"
    assert (
        client.checkout.Session.calls[0]["line_items"][0]["price"]
        == "price_starter_123"
    )
    expected_metadata = {
        "clerk_user_id": "clerk_123",
        "user_id": "user_123",
        "plan_tier": "starter",
        "lifecycle_generation": "7",
    }
    assert client.checkout.Session.calls[0]["metadata"] == expected_metadata
    assert (
        client.checkout.Session.calls[0]["subscription_data"]["metadata"]
        == expected_metadata
    )
    assert telemetry.calls == [("stripe", "create_checkout_session", "success")]


@pytest.mark.anyio
async def test_reactivation_checkout_reuses_customer_and_durable_idempotency() -> None:
    from app.services.billing_session_service import BillingSessionService

    client = FakeStripeClient()
    service = BillingSessionService(
        stripe_client=client,
        price_starter="price_starter_123",
        checkout_success_url="https://app.example.com/success",
        checkout_cancel_url="https://app.example.com/cancel",
    )

    result = await service.create_checkout_session(
        user_id="user_123",
        customer_email="billing@example.com",
        customer_id="cus_retained",
        clerk_user_id="clerk_123",
        plan_tier="starter",
        lifecycle_generation=7,
        idempotency_key="checkout:user_123:g7",
    )

    assert result.provider_session_id == "cs_checkout_123"
    call = client.checkout.Session.calls[0]
    assert call["customer"] == "cus_retained"
    assert "customer_email" not in call
    assert call["idempotency_key"] == "checkout:user_123:g7"


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
        billing_portal_configuration_id="bpc_period_end_cancel",
    )

    with pytest.raises(ValueError, match="Unsupported plan tier: standard"):
        await service.create_checkout_session(
            user_id="user_123",
            customer_email="billing@example.com",
            clerk_user_id="clerk_123",
            plan_tier="standard",
            lifecycle_generation=7,
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
        billing_portal_configuration_id="bpc_period_end_cancel",
    )

    with pytest.raises(ValueError, match="Stripe customer ID is required"):
        await service.create_portal_session(
            customer_id=None, return_url="https://app.example.com/settings"
        )


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
        billing_portal_configuration_id="bpc_period_end_cancel",
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
            "configuration": "bpc_period_end_cancel",
        }
    ]


@pytest.mark.anyio
async def test_create_portal_session_accepts_omitted_caller_return_url() -> None:
    from app.services.billing_session_service import BillingSessionService

    client = FakeStripeClient()
    service = BillingSessionService(
        stripe_client=client,
        billing_portal_return_url="https://app.example.com/dashboard/billing",
        billing_portal_configuration_id="bpc_period_end_cancel",
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
        billing_portal_configuration_id="bpc_period_end_cancel",
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
        lifecycle_generation=7,
    )

    assert heartbeat.done()
    await heartbeat


def test_stripe_sdk_uses_bounded_network_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.billing_session_service import BillingSessionService

    monkeypatch.setattr(stripe, "default_http_client", None)
    monkeypatch.setattr(stripe, "max_network_retries", 0)
    service = BillingSessionService(secret_key="sk_test_123")

    client = service._get_client()

    assert client.max_network_retries == 2
    assert client.default_http_client._timeout == (5, 30)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("provider_error", "expected_disposition", "expected_error_class"),
    [
        (TimeoutError("timeout secret"), "retryable", "timeout"),
        (
            stripe.error.APIConnectionError(
                "retryable connection secret",
                should_retry=True,
            ),
            "retryable",
            "unavailable",
        ),
        (
            stripe.error.APIConnectionError(
                "terminal connection secret",
                should_retry=False,
            ),
            "terminal",
            "unavailable",
        ),
        (
            stripe.error.RateLimitError("rate limit secret"),
            "retryable",
            "rate_limited",
        ),
        (
            stripe.error.APIError("server secret", http_status=503),
            "retryable",
            "unavailable",
        ),
        (
            stripe.error.APIError("gateway timeout secret", http_status=504),
            "retryable",
            "timeout",
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
            stripe.error.InvalidRequestError("validation secret", "customer"),
            "terminal",
            "validation",
        ),
        (
            stripe.error.APIError("client secret", http_status=404),
            "terminal",
            "not_found",
        ),
        (stripe.error.APIError("conflict secret", http_status=409), "terminal", "conflict"),
        (stripe.error.APIError("validation secret", http_status=422), "terminal", "validation"),
        (stripe.error.StripeError("base secret"), "terminal", "unknown"),
    ],
)
@pytest.mark.parametrize(
    ("caller", "operation"),
    [
        ("checkout", "create_checkout_session"),
        ("portal", "create_portal_session"),
    ],
)
async def test_hosted_session_errors_match_shared_stripe_contract(
    provider_error: Exception,
    expected_disposition: str,
    expected_error_class: str,
    caller: str,
    operation: str,
) -> None:
    from app.services.billing_session_service import BillingSessionService

    service = BillingSessionService(
        stripe_client=FailingStripeClient(provider_error, caller=caller),
        price_starter="price_starter_123",
        checkout_success_url="https://app.example.com/success",
        checkout_cancel_url="https://app.example.com/cancel",
        billing_portal_return_url="https://app.example.com/dashboard/billing",
        billing_portal_configuration_id="bpc_period_end_cancel",
    )

    with pytest.raises(ProviderFailure) as exc_info:
        if caller == "checkout":
            await service.create_checkout_session(
                user_id="user_123",
                customer_email="billing@example.com",
                clerk_user_id="clerk_123",
                plan_tier="starter",
                lifecycle_generation=7,
            )
        else:
            await service.create_portal_session(customer_id="cus_123")

    assert exc_info.value.provider == "stripe"
    assert exc_info.value.operation == operation
    assert exc_info.value.disposition == expected_disposition
    assert exc_info.value.error_class == expected_error_class
    assert exc_info.value.__cause__ is provider_error
    assert "secret" not in str(exc_info.value)
    assert "secret" not in repr(exc_info.value)


@pytest.mark.anyio
@pytest.mark.parametrize("defect", [TypeError("TYPE_SENTINEL"), RuntimeError("RUNTIME_SENTINEL")])
@pytest.mark.parametrize("caller", ["checkout", "portal"])
async def test_hosted_session_arbitrary_defects_propagate_unchanged(
    defect: Exception,
    caller: str,
) -> None:
    from app.services.billing_session_service import BillingSessionService

    service = BillingSessionService(
        stripe_client=FailingStripeClient(defect, caller=caller),
        price_starter="price_starter_123",
        checkout_success_url="https://app.example.com/success",
        checkout_cancel_url="https://app.example.com/cancel",
        billing_portal_return_url="https://app.example.com/dashboard/billing",
        billing_portal_configuration_id="bpc_period_end_cancel",
    )

    with pytest.raises(type(defect)) as exc_info:
        if caller == "checkout":
            await service.create_checkout_session(
                user_id="user_123",
                customer_email="billing@example.com",
                clerk_user_id="clerk_123",
                plan_tier="starter",
                lifecycle_generation=7,
            )
        else:
            await service.create_portal_session(customer_id="cus_123")

    assert exc_info.value is defect


@pytest.mark.anyio
async def test_hosted_session_cancellation_propagates_unchanged() -> None:
    from app.services.billing_session_service import BillingSessionService

    service = BillingSessionService(
        stripe_client=FailingStripeClient(asyncio.CancelledError()),
        price_starter="price_starter_123",
        checkout_success_url="https://app.example.com/success",
        checkout_cancel_url="https://app.example.com/cancel",
    )

    with pytest.raises(asyncio.CancelledError):
        await service.create_checkout_session(
            user_id="user_123",
            customer_email="billing@example.com",
            clerk_user_id="clerk_123",
            plan_tier="starter",
            lifecycle_generation=7,
        )


@pytest.mark.anyio
@pytest.mark.parametrize("caller", ["checkout", "portal"])
async def test_malformed_hosted_session_response_is_terminal_validation(caller: str) -> None:
    from app.services.billing_session_service import BillingSessionService

    client = FakeStripeClient()
    malformed_api = type("MalformedSessionAPI", (), {"create": lambda self, **_kwargs: object()})()
    if caller == "checkout":
        client.checkout = type("CheckoutNamespace", (), {"Session": malformed_api})()
    else:
        client.billing_portal = type("BillingPortalNamespace", (), {"Session": malformed_api})()
    service = BillingSessionService(
        stripe_client=client,
        price_starter="price_starter_123",
        checkout_success_url="https://app.example.com/success",
        checkout_cancel_url="https://app.example.com/cancel",
        billing_portal_return_url="https://app.example.com/dashboard/billing",
        billing_portal_configuration_id="bpc_period_end_cancel",
    )

    with pytest.raises(ProviderFailure) as exc_info:
        if caller == "checkout":
            await service.create_checkout_session(
                user_id="user_123",
                customer_email="billing@example.com",
                clerk_user_id="clerk_123",
                plan_tier="starter",
                lifecycle_generation=7,
            )
        else:
            await service.create_portal_session(customer_id="cus_123")

    assert (exc_info.value.provider, exc_info.value.operation) == (
        "stripe",
        "create_checkout_session" if caller == "checkout" else "create_portal_session",
    )
    assert (exc_info.value.disposition, exc_info.value.error_class) == ("terminal", "validation")
