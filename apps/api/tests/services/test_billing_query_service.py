import pytest
from datetime import UTC, datetime
from types import SimpleNamespace

from sqlalchemy import func, select

from app.models.billing_checkout_attempt import BillingCheckoutAttempt
from app.models.phone_number_provisioning import PhoneNumberProvisioning
from app.models.provider_cleanup_operation import ProviderCleanupOperation
from app.models.subscription import Subscription


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
    [("active", False), ("canceled", False)],
)
async def test_get_subscription_without_account_repositories_fails_checkout_closed(
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
        cancel_at_period_end=False,
        cancellation_effective_at=None,
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


@pytest.mark.anyio
async def test_prepare_checkout_reuses_retained_customer_and_attempt(
    db_session,
    active_user,
) -> None:
    from app.services.billing_query_service import BillingQueryService

    active_user.status = "inactive"
    active_user.lifecycle_generation = 2
    db_session.add(
        Subscription(
            user_id=active_user.id,
            stripe_customer_id="cus_retained",
            stripe_subscription_id="sub_canceled",
            plan_tier="starter",
            status="canceled",
            allocated_minutes=60,
            lifecycle_generation=1,
        )
    )
    await db_session.commit()
    service = BillingQueryService(db_session)

    first = await service.prepare_checkout_attempt(active_user.id)
    repeated = await service.prepare_checkout_attempt(active_user.id)

    assert first.allowed is True
    assert repeated.attempt_id == first.attempt_id
    assert first.lifecycle_generation == 2
    assert first.stripe_customer_id == "cus_retained"
    assert first.idempotency_key == f"billing.checkout:{active_user.id}:g2"
    assert first.existing_session_id is None
    assert (
        await db_session.scalar(
            select(func.count()).select_from(BillingCheckoutAttempt)
        )
        == 1
    )


@pytest.mark.anyio
@pytest.mark.parametrize("unresolved_work", ["provider_cleanup", "provisioning"])
async def test_prepare_checkout_blocks_unresolved_prior_provider_work(
    db_session,
    active_user,
    unresolved_work: str,
) -> None:
    from app.services.billing_query_service import BillingQueryService

    active_user.status = "inactive"
    active_user.lifecycle_generation = 2
    db_session.add(
        Subscription(
            user_id=active_user.id,
            stripe_customer_id="cus_retained_blocked",
            stripe_subscription_id="sub_canceled_blocked",
            plan_tier="starter",
            status="canceled",
            allocated_minutes=60,
            lifecycle_generation=1,
        )
    )
    if unresolved_work == "provider_cleanup":
        db_session.add(
            ProviderCleanupOperation(
                user_id=active_user.id,
                lifecycle_generation=1,
                resource_type="stripe_subscription",
                provider_resource_id="sub-stale-unresolved",
                status="pending",
            )
        )
    else:
        db_session.add(
            PhoneNumberProvisioning(
                user_id=active_user.id,
                target_country_code="FR",
                status="running",
                attempt_count=1,
                can_retry=False,
                provider_operation_key="activation:provision:prior-generation",
            )
        )
    await db_session.commit()

    preparation = await BillingQueryService(db_session).prepare_checkout_attempt(
        active_user.id
    )

    assert preparation.allowed is False
    assert (
        await db_session.scalar(
            select(func.count()).select_from(BillingCheckoutAttempt)
        )
        == 0
    )
