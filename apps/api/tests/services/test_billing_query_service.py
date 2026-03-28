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
