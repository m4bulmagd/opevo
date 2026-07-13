import pytest
from datetime import UTC, datetime
from types import SimpleNamespace


class FakeSubscriptionRepository:
    def __init__(self, subscription=None) -> None:
        self.subscription = subscription

    async def get_by_user_id(self, user_id):
        return self.subscription


class FakeUsageRepository:
    def __init__(self, *, balance: int = 0, ledger_entries: list | None = None) -> None:
        self.balance = balance
        self.ledger_entries = ledger_entries or []

    async def get_current_balance(self, *, user_id):
        return self.balance

    async def list_recent_by_user_id(self, *, user_id, limit: int):
        return self.ledger_entries[:limit]


@pytest.mark.anyio
async def test_get_usage_snapshot_returns_subscription_and_balance() -> None:
    from app.services.billing_query_service import BillingQueryService

    subscription = SimpleNamespace(
        plan_tier="starter",
        status="active",
        allocated_minutes=60,
        current_period_start=datetime(2026, 3, 1, tzinfo=UTC),
        current_period_end=datetime(2026, 4, 1, tzinfo=UTC),
    )

    service = BillingQueryService(
        subscription_repository=FakeSubscriptionRepository(subscription),
        usage_repository=FakeUsageRepository(balance=58),
    )

    result = await service.get_usage_snapshot("user_123")

    assert result.minutes_remaining == 58
    assert result.plan_tier == "starter"


@pytest.mark.anyio
async def test_get_subscription_returns_none_when_missing() -> None:
    from app.services.billing_query_service import BillingQueryService

    service = BillingQueryService(
        subscription_repository=FakeSubscriptionRepository(),
        usage_repository=FakeUsageRepository(),
    )

    result = await service.get_subscription("user_123")

    assert result is None


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("status", "can_start_checkout"),
    [("active", False), ("canceled", True)],
)
async def test_get_subscription_exposes_checkout_eligibility(
    status: str,
    can_start_checkout: bool,
) -> None:
    from app.services.billing_query_service import BillingQueryService

    subscription = SimpleNamespace(
        plan_tier="starter",
        status=status,
        allocated_minutes=60,
        current_period_start=None,
        current_period_end=None,
        stripe_customer_id="cus_policy",
        stripe_subscription_id="sub_policy",
    )
    service = BillingQueryService(
        subscription_repository=FakeSubscriptionRepository(subscription),
        usage_repository=FakeUsageRepository(),
    )

    result = await service.get_subscription("user_123")

    assert result is not None
    assert result.can_start_checkout is can_start_checkout


@pytest.mark.anyio
async def test_get_usage_ledger_returns_newest_first_with_limit() -> None:
    from app.services.billing_query_service import BillingQueryService

    ledger_entries = [
        SimpleNamespace(
            id="ledger_3",
            event_type="call_completed",
            minutes_delta=-1,
            balance_after=57,
            call_id="call_3",
            created_at=datetime(2026, 3, 28, 12, 3, tzinfo=UTC),
        ),
        SimpleNamespace(
            id="ledger_2",
            event_type="invoice_paid_reset",
            minutes_delta=60,
            balance_after=58,
            call_id=None,
            created_at=datetime(2026, 3, 28, 12, 2, tzinfo=UTC),
        ),
        SimpleNamespace(
            id="ledger_1",
            event_type="subscription_activated",
            minutes_delta=60,
            balance_after=60,
            call_id=None,
            created_at=datetime(2026, 3, 28, 12, 1, tzinfo=UTC),
        ),
    ]
    service = BillingQueryService(
        subscription_repository=FakeSubscriptionRepository(),
        usage_repository=FakeUsageRepository(ledger_entries=ledger_entries),
    )

    result = await service.get_usage_ledger("user_123", limit=2)

    assert [entry.id for entry in result.entries] == ["ledger_3", "ledger_2"]
