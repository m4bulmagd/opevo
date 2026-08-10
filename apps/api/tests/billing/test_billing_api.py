from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

import pytest
from fastapi import HTTPException

from app.auth.domain import AuthenticatedUser
from app.core.provider_failures import ProviderFailure
from app.schemas.billing_api import (
    UsageLedgerEntryResponse,
    UsageLedgerListResponse,
    UsageSnapshotResponse,
)


def _as_any(value: object) -> Any:
    return cast(Any, value)


def _fake_request():
    """Minimal request-like object for rate-limited endpoints called directly."""
    req = SimpleNamespace()
    req.client = SimpleNamespace(host="127.0.0.1")
    req.state = SimpleNamespace()
    req.scope = {"type": "http"}
    req.url = SimpleNamespace(path="/test")
    req.app = SimpleNamespace(state=SimpleNamespace(limiter=None))
    return req


class FakeBillingQueryService:
    def __init__(
        self,
        *,
        account_status: str = "active",
        lifecycle_generation: int = 1,
        has_incomplete_deactivation: bool = False,
        has_phone: bool = False,
    ) -> None:
        self.usage_limits: list[int] = []
        self.business_transaction_active = True
        self.account_status = account_status
        self.lifecycle_generation = lifecycle_generation
        self.has_incomplete_deactivation = has_incomplete_deactivation
        self.has_phone = has_phone
        self.checkout_attempt_id = UUID("00000000-0000-0000-0000-000000000099")
        self.checkout_session_id: str | None = None

    async def end_business_transaction(self) -> None:
        self.business_transaction_active = False

    async def get_subscription(self, user_id):
        return None

    async def get_checkout_eligibility(self, user_id):
        from app.services.billing_query_service import CheckoutEligibility
        from app.services.subscription_access_policy import SubscriptionAccessPolicy

        subscription = await self.get_subscription(user_id)
        subscription_status = subscription.status if subscription is not None else None
        return CheckoutEligibility(
            allowed=SubscriptionAccessPolicy.can_start_checkout(
                account_status=self.account_status,
                subscription_status=subscription_status,
                has_incomplete_deactivation=self.has_incomplete_deactivation,
                has_phone=self.has_phone,
            ),
            lifecycle_generation=self.lifecycle_generation,
        )

    async def prepare_checkout_attempt(self, user_id):
        from app.services.billing_query_service import CheckoutAttemptPreparation

        eligibility = await self.get_checkout_eligibility(user_id)
        subscription = await self.get_subscription(user_id)
        self.business_transaction_active = False
        return CheckoutAttemptPreparation(
            allowed=eligibility.allowed,
            lifecycle_generation=eligibility.lifecycle_generation,
            attempt_id=self.checkout_attempt_id if eligibility.allowed else None,
            idempotency_key=(
                f"billing.checkout:{user_id}:g{eligibility.lifecycle_generation}"
                if eligibility.allowed
                else None
            ),
            existing_session_id=self.checkout_session_id,
            stripe_customer_id=(
                subscription.stripe_customer_id
                if subscription is not None
                else None
            ),
        )

    async def complete_checkout_attempt(
        self,
        *,
        attempt_id,
        stripe_checkout_session_id,
    ) -> None:
        assert attempt_id == self.checkout_attempt_id
        if (
            self.checkout_session_id is not None
            and self.checkout_session_id != stripe_checkout_session_id
        ):
            raise ValueError("Stripe checkout session identity conflict")
        self.checkout_session_id = stripe_checkout_session_id

    async def get_usage_snapshot(self, user_id):
        return UsageSnapshotResponse(
            minutes_remaining=0,
            allocated_minutes=0,
            plan_tier=None,
            subscription_status=None,
            current_period_start=None,
            current_period_end=None,
        )

    async def get_usage_ledger(self, user_id, *, limit: int):
        self.usage_limits.append(limit)
        return UsageLedgerListResponse(
            entries=[
                UsageLedgerEntryResponse(
                    id="ledger_2",
                    event_type="call_completed",
                    minutes_delta=-1,
                    balance_after=59,
                    call_id="call_2",
                    created_at="2026-03-28T10:02:00Z",
                ),
                UsageLedgerEntryResponse(
                    id="ledger_1",
                    event_type="subscription_activated",
                    minutes_delta=60,
                    balance_after=60,
                    call_id=None,
                    created_at="2026-03-28T10:01:00Z",
                ),
            ]
        )


@pytest.mark.anyio
async def test_get_subscription_returns_null_for_new_user() -> None:
    from app.routers.billing import get_subscription

    response = await get_subscription(
        identity=_as_any(
            AuthenticatedUser(
                internal_user_id=UUID("00000000-0000-0000-0000-000000000000"),
            )
        ),
        service=_as_any(FakeBillingQueryService()),
    )

    assert response is None


@pytest.mark.anyio
async def test_get_usage_returns_zeroed_snapshot_without_subscription() -> None:
    from app.routers.billing import get_usage

    response = await get_usage(
        identity=_as_any(
            AuthenticatedUser(
                internal_user_id=UUID("00000000-0000-0000-0000-000000000000"),
            )
        ),
        service=_as_any(FakeBillingQueryService()),
    )

    assert response.minutes_remaining == 0
    assert response.allocated_minutes == 0
    assert response.plan_tier is None


@pytest.mark.anyio
async def test_get_usage_ledger_returns_recent_entries() -> None:
    from app.routers.billing import get_usage_ledger

    service = FakeBillingQueryService()
    response = await get_usage_ledger(
        identity=_as_any(
            AuthenticatedUser(
                internal_user_id=UUID("00000000-0000-0000-0000-000000000000"),
            )
        ),
        limit=2,
        service=_as_any(service),
    )

    assert [entry.id for entry in response.entries] == ["ledger_2", "ledger_1"]
    assert service.usage_limits == [2]


class FakeBillingSessionService:
    def __init__(
        self,
        *,
        checkout_url="https://checkout.stripe.test/session",
        portal_url="https://billing.stripe.test/session",
        query_service: FakeBillingQueryService | None = None,
    ) -> None:
        self.checkout_url = checkout_url
        self.portal_url = portal_url
        self.query_service = query_service
        self.checkout_generations: list[int] = []
        self.checkout_calls: list[dict] = []

    def _assert_transaction_ended(self) -> None:
        if self.query_service is not None:
            assert self.query_service.business_transaction_active is False

    async def create_checkout_session(
        self,
        *,
        user_id,
        customer_email,
        plan_tier,
        lifecycle_generation,
        customer_id=None,
        idempotency_key=None,
        existing_session_id=None,
    ):
        self._assert_transaction_ended()
        if plan_tier != "starter":
            raise ValueError(f"Unsupported plan tier: {plan_tier}")
        self.checkout_generations.append(lifecycle_generation)
        self.checkout_calls.append(
            {
                "customer_email": customer_email,
                "customer_id": customer_id,
                "idempotency_key": idempotency_key,
                "existing_session_id": existing_session_id,
            }
        )
        return type(
            "HostedSession",
            (),
            {
                "url": self.checkout_url,
                "provider_session_id": existing_session_id or "cs_durable",
            },
        )()

    async def create_portal_session(self, *, customer_id, return_url):
        self._assert_transaction_ended()
        return type("HostedSession", (), {"url": self.portal_url})()


class FakeUnsafePortalSessionService(FakeBillingSessionService):
    async def create_portal_session(self, *, customer_id, return_url):
        from app.services.billing_session_service import BillingPortalReturnUrlError

        raise BillingPortalReturnUrlError("unsafe return URL")


class FakeProviderFailingBillingSessionService(FakeBillingSessionService):
    async def create_checkout_session(self, **_kwargs):
        failure = ProviderFailure(
            provider="stripe",
            operation="create_checkout_session",
            disposition="terminal",
            error_class="validation",
        )
        raise failure from RuntimeError("RAW_STRIPE_BODY_AND_TOKEN_SENTINEL")

    async def create_portal_session(self, **_kwargs):
        failure = ProviderFailure(
            provider="stripe",
            operation="create_portal_session",
            disposition="terminal",
            error_class="validation",
        )
        raise failure from RuntimeError("RAW_STRIPE_BODY_AND_TOKEN_SENTINEL")


class FakeEmptySubscriptionQueryService(FakeBillingQueryService):
    async def get_subscription(self, user_id):
        return None


class FakeActiveSubscriptionQueryService(FakeBillingQueryService):
    async def get_subscription(self, user_id):
        from app.schemas.billing_api import SubscriptionResponse

        return SubscriptionResponse(
            plan_tier="starter",
            status="active",
            allocated_minutes=60,
            current_period_start=datetime(2026, 3, 1, tzinfo=UTC),
            current_period_end=datetime(2026, 4, 1, tzinfo=UTC),
            stripe_customer_id="cus_123",
            stripe_subscription_id="sub_123",
            can_start_checkout=False,
            cancel_at_period_end=False,
            cancellation_effective_at=None,
        )


class FakeStatusSubscriptionQueryService(FakeActiveSubscriptionQueryService):
    def __init__(self, subscription_status: str) -> None:
        super().__init__()
        self.subscription_status = subscription_status

    async def get_subscription(self, user_id):
        subscription = await super().get_subscription(user_id)
        return subscription.model_copy(update={"status": self.subscription_status})


@pytest.mark.anyio
async def test_create_checkout_session_returns_url() -> None:
    from app.routers.billing import create_checkout_session
    from app.schemas.billing_api import CheckoutSessionRequest

    response = await create_checkout_session(
        request=_fake_request(),
        payload=CheckoutSessionRequest(plan_tier="starter"),
        identity=AuthenticatedUser(
            internal_user_id=UUID("00000000-0000-0000-0000-000000000000"),
        ),
        service=FakeBillingSessionService(),
        query_service=FakeEmptySubscriptionQueryService(),
        user=type("User", (), {"email": "billing@example.com"})(),
    )

    assert response.url == "https://checkout.stripe.test/session"


@pytest.mark.anyio
async def test_checkout_provider_failure_keeps_the_http_response_safe() -> None:
    from app.routers.billing import create_checkout_session
    from app.schemas.billing_api import CheckoutSessionRequest

    with pytest.raises(HTTPException) as exc_info:
        await create_checkout_session(
            request=_fake_request(),
            payload=CheckoutSessionRequest(plan_tier="starter"),
            identity=AuthenticatedUser(
                internal_user_id=UUID("00000000-0000-0000-0000-000000000000"),
            ),
            service=FakeProviderFailingBillingSessionService(),
            query_service=FakeEmptySubscriptionQueryService(),
            user=type("User", (), {"email": "billing@example.com"})(),
        )

    assert (exc_info.value.status_code, exc_info.value.detail) == (
        502,
        "Failed to create Stripe checkout session",
    )
    assert "RAW_STRIPE_BODY_AND_TOKEN_SENTINEL" not in str(exc_info.value.detail)


@pytest.mark.anyio
async def test_checkout_ends_business_transaction_before_stripe() -> None:
    from app.routers.billing import create_checkout_session
    from app.schemas.billing_api import CheckoutSessionRequest

    query_service = FakeEmptySubscriptionQueryService()
    service = FakeBillingSessionService(query_service=query_service)
    response = await create_checkout_session(
        request=_fake_request(),
        payload=CheckoutSessionRequest(plan_tier="starter"),
        identity=AuthenticatedUser(
            internal_user_id=UUID("00000000-0000-0000-0000-000000000000"),
        ),
        service=service,
        query_service=query_service,
        user=type("User", (), {"email": "billing@example.com"})(),
    )

    assert response.url == "https://checkout.stripe.test/session"
    assert service.checkout_generations == [1]


@pytest.mark.anyio
async def test_checkout_uses_generation_captured_by_locked_eligibility() -> None:
    from app.routers.billing import create_checkout_session
    from app.schemas.billing_api import CheckoutSessionRequest

    query_service = FakeBillingQueryService(
        account_status="inactive",
        lifecycle_generation=4,
    )
    service = FakeBillingSessionService(query_service=query_service)

    await create_checkout_session(
        request=_fake_request(),
        payload=CheckoutSessionRequest(plan_tier="starter"),
        identity=AuthenticatedUser(
            internal_user_id=UUID("00000000-0000-0000-0000-000000000000"),
        ),
        service=service,
        query_service=query_service,
        user=type("User", (), {"email": "billing@example.com"})(),
    )

    assert query_service.business_transaction_active is False
    assert service.checkout_generations == [4]


@pytest.mark.anyio
async def test_reactivation_checkout_reuses_customer_and_same_durable_session() -> None:
    from app.routers.billing import create_checkout_session
    from app.schemas.billing_api import CheckoutSessionRequest

    query_service = FakeStatusSubscriptionQueryService("canceled")
    query_service.account_status = "inactive"
    query_service.lifecycle_generation = 4
    service = FakeBillingSessionService(query_service=query_service)
    identity = AuthenticatedUser(
        internal_user_id=UUID("00000000-0000-0000-0000-000000000000"),
    )
    payload = CheckoutSessionRequest(plan_tier="starter")

    first = await create_checkout_session(
        request=_fake_request(),
        payload=payload,
        identity=identity,
        service=service,
        query_service=query_service,
        user=type("User", (), {"email": "billing@example.com"})(),
    )
    repeated = await create_checkout_session(
        request=_fake_request(),
        payload=payload,
        identity=identity,
        service=service,
        query_service=query_service,
        user=type("User", (), {"email": "billing@example.com"})(),
    )

    assert first.url == repeated.url
    assert service.checkout_calls == [
        {
            "customer_email": "billing@example.com",
            "customer_id": "cus_123",
            "idempotency_key": (
                "billing.checkout:00000000-0000-0000-0000-000000000000:g4"
            ),
            "existing_session_id": None,
        },
        {
            "customer_email": "billing@example.com",
            "customer_id": "cus_123",
            "idempotency_key": (
                "billing.checkout:00000000-0000-0000-0000-000000000000:g4"
            ),
            "existing_session_id": "cs_durable",
        },
    ]


def test_checkout_request_rejects_standard_plan() -> None:
    from pydantic import ValidationError

    from app.schemas.billing_api import CheckoutSessionRequest

    with pytest.raises(ValidationError):
        CheckoutSessionRequest(plan_tier="standard")


@pytest.mark.anyio
async def test_create_checkout_session_rejects_active_subscription() -> None:
    from app.routers.billing import create_checkout_session
    from app.schemas.billing_api import CheckoutSessionRequest

    with pytest.raises(HTTPException) as exc_info:
        await create_checkout_session(
            request=_fake_request(),
            payload=CheckoutSessionRequest(plan_tier="starter"),
            identity=AuthenticatedUser(
                internal_user_id=UUID("00000000-0000-0000-0000-000000000000"),
            ),
            service=FakeBillingSessionService(),
            query_service=FakeActiveSubscriptionQueryService(),
            user=type("User", (), {"email": "billing@example.com"})(),
        )

    assert exc_info.value.status_code == 409


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("subscription_status", "allowed"),
    [
        ("canceled", True),
        ("incomplete_expired", True),
        ("trialing", False),
        ("active", False),
        ("past_due", False),
        ("unpaid", False),
        ("incomplete", False),
        ("paused", False),
    ],
)
async def test_checkout_uses_central_subscription_eligibility(
    subscription_status: str,
    allowed: bool,
) -> None:
    from app.routers.billing import create_checkout_session
    from app.schemas.billing_api import CheckoutSessionRequest

    call = create_checkout_session(
        request=_fake_request(),
        payload=CheckoutSessionRequest(plan_tier="starter"),
        identity=AuthenticatedUser(
            internal_user_id=UUID("00000000-0000-0000-0000-000000000000"),
        ),
        service=FakeBillingSessionService(),
        query_service=FakeStatusSubscriptionQueryService(subscription_status),
        user=type("User", (), {"email": "billing@example.com"})(),
    )

    if allowed:
        assert (await call).url == "https://checkout.stripe.test/session"
    else:
        with pytest.raises(HTTPException) as exc_info:
            await call
        assert exc_info.value.status_code == 409


@pytest.mark.anyio
@pytest.mark.parametrize(
    (
        "account_status",
        "has_incomplete_deactivation",
        "has_phone",
        "allowed",
    ),
    [
        ("deactivating", False, False, False),
        ("deactivating", True, True, False),
        ("inactive", True, False, False),
        ("inactive", False, True, False),
        ("inactive", False, False, True),
    ],
)
async def test_checkout_enforces_account_reactivation_preconditions(
    account_status: str,
    has_incomplete_deactivation: bool,
    has_phone: bool,
    allowed: bool,
) -> None:
    from app.routers.billing import create_checkout_session
    from app.schemas.billing_api import CheckoutSessionRequest

    query_service = FakeStatusSubscriptionQueryService("canceled")
    query_service.account_status = account_status
    query_service.has_incomplete_deactivation = has_incomplete_deactivation
    query_service.has_phone = has_phone
    call = create_checkout_session(
        request=_fake_request(),
        payload=CheckoutSessionRequest(plan_tier="starter"),
        identity=AuthenticatedUser(
            internal_user_id=UUID("00000000-0000-0000-0000-000000000000"),
        ),
        service=FakeBillingSessionService(),
        query_service=query_service,
        user=type("User", (), {"email": "billing@example.com"})(),
    )

    if allowed:
        assert (await call).url == "https://checkout.stripe.test/session"
    else:
        with pytest.raises(HTTPException) as exc_info:
            await call
        assert exc_info.value.status_code == 409


@pytest.mark.anyio
async def test_create_portal_session_returns_url() -> None:
    from app.routers.billing import create_portal_session
    from app.schemas.billing_api import PortalSessionRequest

    response = await create_portal_session(
        request=_fake_request(),
        payload=PortalSessionRequest(return_url="https://app.example.com/settings"),
        identity=AuthenticatedUser(
            internal_user_id=UUID("00000000-0000-0000-0000-000000000000"),
        ),
        service=FakeBillingSessionService(),
        query_service=FakeActiveSubscriptionQueryService(),
    )

    assert response.url == "https://billing.stripe.test/session"


@pytest.mark.anyio
async def test_portal_provider_failure_keeps_the_http_response_safe() -> None:
    from app.routers.billing import create_portal_session
    from app.schemas.billing_api import PortalSessionRequest

    with pytest.raises(HTTPException) as exc_info:
        await create_portal_session(
            request=_fake_request(),
            payload=PortalSessionRequest(),
            identity=AuthenticatedUser(
                internal_user_id=UUID("00000000-0000-0000-0000-000000000000"),
            ),
            service=FakeProviderFailingBillingSessionService(),
            query_service=FakeActiveSubscriptionQueryService(),
        )

    assert (exc_info.value.status_code, exc_info.value.detail) == (
        502,
        "Failed to create Stripe billing portal session",
    )
    assert "RAW_STRIPE_BODY_AND_TOKEN_SENTINEL" not in str(exc_info.value.detail)


@pytest.mark.anyio
async def test_portal_ends_business_transaction_before_stripe() -> None:
    from app.routers.billing import create_portal_session
    from app.schemas.billing_api import PortalSessionRequest

    query_service = FakeActiveSubscriptionQueryService()
    response = await create_portal_session(
        request=_fake_request(),
        payload=PortalSessionRequest(return_url="https://app.example.com/settings"),
        identity=AuthenticatedUser(
            internal_user_id=UUID("00000000-0000-0000-0000-000000000000"),
        ),
        service=FakeBillingSessionService(query_service=query_service),
        query_service=query_service,
    )

    assert response.url == "https://billing.stripe.test/session"


@pytest.mark.anyio
async def test_billing_query_service_rolls_back_autobegun_transaction(
    db_session,
    active_user,
) -> None:
    from app.services.billing_query_service import BillingQueryService

    service = BillingQueryService(db_session)
    await service.get_subscription(active_user.id)
    assert db_session.in_transaction() is True

    await service.end_business_transaction()

    assert db_session.in_transaction() is False


@pytest.mark.anyio
async def test_subscription_query_exposes_scheduled_cancellation(
    db_session,
    active_user,
) -> None:
    from app.models.subscription import Subscription
    from app.services.billing_query_service import BillingQueryService

    effective_at = datetime(2026, 4, 1, tzinfo=UTC)
    db_session.add(
        Subscription(
            user_id=active_user.id,
            stripe_customer_id="cus_scheduled_query",
            stripe_subscription_id="sub_scheduled_query",
            plan_tier="starter",
            status="active",
            allocated_minutes=60,
            lifecycle_generation=1,
            cancel_at_period_end=True,
            cancellation_effective_at=effective_at,
        )
    )
    await db_session.flush()

    response = await BillingQueryService(db_session).get_subscription(active_user.id)

    assert response is not None
    assert response.cancel_at_period_end is True
    assert response.cancellation_effective_at is not None
    assert response.cancellation_effective_at.replace(tzinfo=UTC) == effective_at


@pytest.mark.anyio
async def test_create_portal_session_accepts_omitted_return_url() -> None:
    from app.routers.billing import create_portal_session
    from app.schemas.billing_api import PortalSessionRequest

    response = await create_portal_session(
        request=_fake_request(),
        payload=PortalSessionRequest(),
        identity=AuthenticatedUser(
            internal_user_id=UUID("00000000-0000-0000-0000-000000000000"),
        ),
        service=FakeBillingSessionService(),
        query_service=FakeActiveSubscriptionQueryService(),
    )

    assert response.url == "https://billing.stripe.test/session"


@pytest.mark.anyio
async def test_create_portal_session_maps_unsafe_return_url_to_bad_request() -> None:
    from app.routers.billing import create_portal_session
    from app.schemas.billing_api import PortalSessionRequest

    with pytest.raises(HTTPException) as exc_info:
        await create_portal_session(
            request=_fake_request(),
            payload=PortalSessionRequest(
                return_url="https://evil.example.com/settings"
            ),
            identity=AuthenticatedUser(
                internal_user_id=UUID("00000000-0000-0000-0000-000000000000"),
            ),
            service=FakeUnsafePortalSessionService(),
            query_service=FakeActiveSubscriptionQueryService(),
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Invalid billing portal return URL"


@pytest.mark.anyio
async def test_create_portal_session_rejects_missing_customer() -> None:
    from app.routers.billing import create_portal_session
    from app.schemas.billing_api import PortalSessionRequest

    with pytest.raises(HTTPException) as exc_info:
        await create_portal_session(
            request=_fake_request(),
            payload=PortalSessionRequest(return_url="https://app.example.com/settings"),
            identity=AuthenticatedUser(
                internal_user_id=UUID("00000000-0000-0000-0000-000000000000"),
            ),
            service=FakeBillingSessionService(),
            query_service=FakeEmptySubscriptionQueryService(),
        )

    assert exc_info.value.status_code == 409
