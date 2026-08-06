import asyncio
import builtins
import logging
import time
from contextlib import asynccontextmanager

import pytest
import stripe

from app.core.config import Settings
from app.core.provider_failures import ProviderFailure
from app.services.billing_session_service import (
    BillingSessionService as _BillingSessionService,
)


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


def BillingSessionService(
    *,
    stripe_client=None,
    secret_key: str | None = None,
    price_starter: str | None = None,
    checkout_success_url: str | None = None,
    checkout_cancel_url: str | None = None,
    billing_portal_return_url: str | None = None,
    billing_portal_configuration_id: str | None = None,
    observability=None,
) -> _BillingSessionService:
    settings = Settings(
        database_url="sqlite+aiosqlite://",
        redis_url="redis://localhost:6379/0",
        stripe_secret_key=secret_key,
        stripe_price_starter=price_starter,
        stripe_checkout_success_url=checkout_success_url,
        stripe_checkout_cancel_url=checkout_cancel_url,
        stripe_billing_portal_return_url=billing_portal_return_url,
        stripe_billing_portal_configuration_id=billing_portal_configuration_id,
    )
    return _BillingSessionService(
        settings=settings,
        observability=observability or _Telemetry(),
        stripe_module=stripe_client,
    )


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


class RawLoggingCheckoutSessionAPI(FakeCheckoutSessionAPI):
    def create(self, **kwargs):
        logging.getLogger("stripe").debug(
            "message='Stripe v1 API error received' RAW_HOSTED_STRIPE_SENTINEL"
        )
        return super().create(**kwargs)


class RawLoggingPortalSessionAPI(FakePortalSessionAPI):
    def create(self, **kwargs):
        logging.getLogger("stripe").debug(
            "message='Stripe v2 API error received' RAW_HOSTED_STRIPE_SENTINEL"
        )
        return super().create(**kwargs)


class RawLoggingStripeClient(FakeStripeClient):
    def __init__(self) -> None:
        super().__init__()
        self.checkout = type(
            "CheckoutNamespace", (), {"Session": RawLoggingCheckoutSessionAPI()}
        )()
        self.billing_portal = type(
            "BillingPortalNamespace", (), {"Session": RawLoggingPortalSessionAPI()}
        )()


def _deny_stripe_import(monkeypatch: pytest.MonkeyPatch) -> None:
    original_import = builtins.__import__

    def unavailable_stripe_import(name: str, *args, **kwargs):
        if name == "stripe" or name.startswith("stripe."):
            raise ImportError("STRIPE_SDK_UNAVAILABLE")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", unavailable_stripe_import)


@pytest.mark.anyio
async def test_create_checkout_session_uses_price_mapping() -> None:

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
@pytest.mark.parametrize("caller", ["checkout", "portal"])
async def test_injected_hosted_client_logs_are_filtered_without_hiding_unrelated_logs(
    caller: str,
    caplog: pytest.LogCaptureFixture,
) -> None:

    service = BillingSessionService(
        stripe_client=RawLoggingStripeClient(),
        price_starter="price_starter_123",
        checkout_success_url="https://app.example.com/success",
        checkout_cancel_url="https://app.example.com/cancel",
        billing_portal_return_url="https://app.example.com/dashboard/billing",
        billing_portal_configuration_id="bpc_period_end_cancel",
    )

    with caplog.at_level(logging.DEBUG):
        logging.getLogger("app.unrelated").info("unrelated hosted application log")
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

    assert "RAW_HOSTED_STRIPE_SENTINEL" not in caplog.text
    assert "unrelated hosted application log" in caplog.text


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("caller", "operation"),
    [
        ("checkout", "create_checkout_session"),
        ("portal", "create_portal_session"),
    ],
)
async def test_non_injected_hosted_client_missing_sdk_is_safe_provider_failure(
    caller: str,
    operation: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    _deny_stripe_import(monkeypatch)
    service = BillingSessionService(
        secret_key="sk_test_123",
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

    assert (exc_info.value.provider, exc_info.value.operation) == ("stripe", operation)
    assert (exc_info.value.disposition, exc_info.value.error_class) == (
        "terminal",
        "validation",
    )
    assert isinstance(exc_info.value.__cause__, ImportError)
    assert "STRIPE_SDK_UNAVAILABLE" not in str(exc_info.value)


@pytest.mark.anyio
@pytest.mark.parametrize("caller", ["checkout", "portal"])
async def test_injected_hosted_client_works_and_redacts_logs_without_sdk(
    caller: str,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    _deny_stripe_import(monkeypatch)
    service = BillingSessionService(
        stripe_client=RawLoggingStripeClient(),
        price_starter="price_starter_123",
        checkout_success_url="https://app.example.com/success",
        checkout_cancel_url="https://app.example.com/cancel",
        billing_portal_return_url="https://app.example.com/dashboard/billing",
        billing_portal_configuration_id="bpc_period_end_cancel",
    )

    with caplog.at_level(logging.DEBUG):
        logging.getLogger("app.unrelated").info("unrelated missing sdk log")
        if caller == "checkout":
            result = await service.create_checkout_session(
                user_id="user_123",
                customer_email="billing@example.com",
                clerk_user_id="clerk_123",
                plan_tier="starter",
                lifecycle_generation=7,
            )
        else:
            result = await service.create_portal_session(customer_id="cus_123")

    assert result.url.startswith("https://")
    assert "RAW_HOSTED_STRIPE_SENTINEL" not in caplog.text
    assert "unrelated missing sdk log" in caplog.text


@pytest.mark.anyio
@pytest.mark.parametrize("caller", ["checkout", "portal"])
@pytest.mark.parametrize("defect", [TypeError("TYPE_SENTINEL"), RuntimeError("RUNTIME_SENTINEL")])
async def test_injected_hosted_defect_propagates_unchanged_without_sdk(
    caller: str,
    defect: Exception,
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    _deny_stripe_import(monkeypatch)
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
async def test_reactivation_checkout_reuses_customer_and_durable_idempotency() -> None:

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
    from app.services.billing_session_service import BillingPortalReturnUrlError

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
    service = BillingSessionService(
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
            stripe.error.APIError("request timeout secret", http_status=408),
            "retryable",
            "timeout",
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
            stripe.error.APIError("forbidden secret", http_status=403),
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
