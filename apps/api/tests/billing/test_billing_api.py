from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import HTTPException

from app.core.auth import UserIdentity


def _fake_request():
    """Minimal request-like object for rate-limited endpoints called directly."""
    req = SimpleNamespace()
    req.client = SimpleNamespace(host="127.0.0.1")
    req.state = SimpleNamespace()
    req.scope = {"type": "http"}
    req.url = SimpleNamespace(path="/test")
    req.app = SimpleNamespace(state=SimpleNamespace(limiter=None))
    return req
from app.schemas.billing_api import UsageLedgerEntryResponse, UsageLedgerListResponse, UsageSnapshotResponse


class FakeBillingQueryService:
    def __init__(self) -> None:
        self.usage_limits: list[int] = []

    async def get_subscription(self, user_id):
        return None

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
        identity=UserIdentity(clerk_user_id="user_123", internal_user_id=UUID("00000000-0000-0000-0000-000000000000")),
        service=FakeBillingQueryService(),
    )

    assert response is None


@pytest.mark.anyio
async def test_get_usage_returns_zeroed_snapshot_without_subscription() -> None:
    from app.routers.billing import get_usage

    response = await get_usage(
        identity=UserIdentity(clerk_user_id="user_123", internal_user_id=UUID("00000000-0000-0000-0000-000000000000")),
        service=FakeBillingQueryService(),
    )

    assert response.minutes_remaining == 0
    assert response.allocated_minutes == 0
    assert response.plan_tier is None


@pytest.mark.anyio
async def test_get_usage_ledger_returns_recent_entries() -> None:
    from app.routers.billing import get_usage_ledger

    service = FakeBillingQueryService()
    response = await get_usage_ledger(
        identity=UserIdentity(clerk_user_id="user_123", internal_user_id=UUID("00000000-0000-0000-0000-000000000000")),
        limit=2,
        service=service,
    )

    assert [entry.id for entry in response.entries] == ["ledger_2", "ledger_1"]
    assert service.usage_limits == [2]


class FakeBillingSessionService:
    def __init__(self, *, checkout_url="https://checkout.stripe.test/session", portal_url="https://billing.stripe.test/session") -> None:
        self.checkout_url = checkout_url
        self.portal_url = portal_url

    def create_checkout_session(self, *, user_id, customer_email, clerk_user_id, plan_tier):
        if plan_tier != "starter":
            raise ValueError(f"Unsupported plan tier: {plan_tier}")
        return type("HostedSession", (), {"url": self.checkout_url})()

    def create_portal_session(self, *, customer_id, return_url):
        return type("HostedSession", (), {"url": self.portal_url})()


class FakeUnsafePortalSessionService(FakeBillingSessionService):
    def create_portal_session(self, *, customer_id, return_url):
        from app.services.billing_session_service import BillingPortalReturnUrlError

        raise BillingPortalReturnUrlError("unsafe return URL")


class FakeEmptySubscriptionQueryService:
    async def get_subscription(self, user_id):
        return None


class FakeActiveSubscriptionQueryService:
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
        )


class FakeStatusSubscriptionQueryService(FakeActiveSubscriptionQueryService):
    def __init__(self, subscription_status: str) -> None:
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
        identity=UserIdentity(clerk_user_id="user_123", internal_user_id=UUID("00000000-0000-0000-0000-000000000000")),
        service=FakeBillingSessionService(),
        query_service=FakeEmptySubscriptionQueryService(),
        user=type("User", (), {"email": "billing@example.com"})(),
    )

    assert response.url == "https://checkout.stripe.test/session"


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
            identity=UserIdentity(clerk_user_id="user_123", internal_user_id=UUID("00000000-0000-0000-0000-000000000000")),
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
        identity=UserIdentity(
            clerk_user_id="user_123",
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
async def test_create_portal_session_returns_url() -> None:
    from app.routers.billing import create_portal_session
    from app.schemas.billing_api import PortalSessionRequest

    response = await create_portal_session(
        request=_fake_request(),
        payload=PortalSessionRequest(return_url="https://app.example.com/settings"),
        identity=UserIdentity(clerk_user_id="user_123", internal_user_id=UUID("00000000-0000-0000-0000-000000000000")),
        service=FakeBillingSessionService(),
        query_service=FakeActiveSubscriptionQueryService(),
    )

    assert response.url == "https://billing.stripe.test/session"


@pytest.mark.anyio
async def test_create_portal_session_accepts_omitted_return_url() -> None:
    from app.routers.billing import create_portal_session
    from app.schemas.billing_api import PortalSessionRequest

    response = await create_portal_session(
        request=_fake_request(),
        payload=PortalSessionRequest(),
        identity=UserIdentity(clerk_user_id="user_123", internal_user_id=UUID("00000000-0000-0000-0000-000000000000")),
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
            payload=PortalSessionRequest(return_url="https://evil.example.com/settings"),
            identity=UserIdentity(clerk_user_id="user_123", internal_user_id=UUID("00000000-0000-0000-0000-000000000000")),
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
            identity=UserIdentity(clerk_user_id="user_123", internal_user_id=UUID("00000000-0000-0000-0000-000000000000")),
            service=FakeBillingSessionService(),
            query_service=FakeEmptySubscriptionQueryService(),
        )

    assert exc_info.value.status_code == 409
