from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.models.subscription import Subscription
from app.models.usage_ledger import UsageLedger
from app.repositories.subscription_repository import SubscriptionRepository
from app.repositories.usage_repository import UsageRepository
from app.services.local_billing_service import (
    LocalBillingConflictError,
    LocalBillingService,
)


FIXED_NOW = datetime(2026, 7, 18, 10, 0, tzinfo=UTC)


def _as_utc(value: datetime | None) -> datetime:
    assert value is not None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


@pytest.mark.anyio
async def test_local_billing_activates_starter_and_grants_once(
    db_session,
    active_user,
) -> None:
    service = LocalBillingService(db_session)

    first = await service.activate_starter(active_user.id, now=FIXED_NOW)
    second = await service.activate_starter(
        active_user.id,
        now=FIXED_NOW + timedelta(days=5),
    )

    assert (
        await UsageRepository(db_session).get_current_balance(
            user_id=active_user.id,
        )
        == 60
    )
    assert first.id == second.id
    assert first.stripe_customer_id == f"local_customer_{active_user.id}"
    assert first.stripe_subscription_id == f"local_subscription_{active_user.id}"
    assert first.plan_tier == "starter"
    assert first.status == "active"
    assert first.allocated_minutes == 60
    assert _as_utc(first.current_period_start) == FIXED_NOW
    assert _as_utc(first.current_period_end) == FIXED_NOW + timedelta(days=30)
    assert _as_utc(second.current_period_start) == FIXED_NOW
    assert _as_utc(second.current_period_end) == FIXED_NOW + timedelta(days=30)
    assert (
        await db_session.scalar(
            select(func.count()).select_from(UsageLedger).where(
                UsageLedger.source_id == f"local-starter:{active_user.id}"
            )
        )
        == 1
    )


@pytest.mark.anyio
async def test_local_billing_never_overwrites_real_subscription(
    db_session,
    active_user,
) -> None:
    user_id = active_user.id
    original = Subscription(
        user_id=user_id,
        stripe_customer_id="cus_real",
        stripe_subscription_id="sub_real",
        plan_tier="starter",
        status="active",
        allocated_minutes=60,
        current_period_start=FIXED_NOW,
        current_period_end=FIXED_NOW + timedelta(days=30),
        stripe_subscription_created_at=FIXED_NOW,
        last_stripe_event_created_at=FIXED_NOW,
    )
    db_session.add(original)
    await db_session.commit()

    with pytest.raises(LocalBillingConflictError) as exc_info:
        await LocalBillingService(db_session).activate_starter(
            user_id,
            now=FIXED_NOW + timedelta(days=1),
        )

    assert exc_info.value.code == "real_subscription_present"
    db_session.expire_all()
    persisted = await SubscriptionRepository(db_session).get_by_user_id(user_id)
    assert persisted is not None
    assert persisted.stripe_customer_id == "cus_real"
    assert persisted.stripe_subscription_id == "sub_real"
    assert await UsageRepository(db_session).get_current_balance(
        user_id=user_id
    ) == 0


@pytest.mark.anyio
async def test_local_billing_rolls_back_subscription_when_grant_fails(
    db_session,
    active_user,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = active_user.id
    service = LocalBillingService(db_session)

    async def fail_grant(**_kwargs):
        raise RuntimeError("synthetic grant failed")

    monkeypatch.setattr(service.usage_accounting_service, "grant_invoice", fail_grant)

    with pytest.raises(RuntimeError, match="synthetic grant failed"):
        await service.activate_starter(user_id, now=FIXED_NOW)

    assert await SubscriptionRepository(db_session).get_by_user_id(user_id) is None
    assert await UsageRepository(db_session).get_current_balance(
        user_id=user_id
    ) == 0


@pytest.mark.anyio
async def test_local_billing_preserves_global_grant_lock_order(
) -> None:
    events: list[str] = []
    user_id = uuid4()
    user = SimpleNamespace(id=user_id)
    subscription = SimpleNamespace(
        id=user_id,
        user_id=user_id,
        stripe_customer_id=f"local_customer_{user_id}",
        stripe_subscription_id=f"local_subscription_{user_id}",
        plan_tier="starter",
        status="active",
        allocated_minutes=60,
        current_period_start=FIXED_NOW,
        current_period_end=FIXED_NOW + timedelta(days=30),
    )

    class Usage:
        async def acquire_invoice_grant_lock(self, *, invoice_id: str) -> None:
            assert invoice_id == f"local-starter:{user_id}"
            events.append("grant_advisory_lock")

        async def grant_invoice(self, **_kwargs):
            events.append("usage_grant")
            return SimpleNamespace(already_granted=False)

    class Users:
        async def get_by_id_for_update(self, requested_user_id):
            assert requested_user_id == user_id
            events.append("user_lock")
            return user

    class Subscriptions:
        async def get_by_user_id_for_update(self, requested_user_id):
            assert requested_user_id == user_id
            events.append("subscription_lock")
            return None

        async def upsert_by_stripe_subscription_id(self, **_kwargs):
            events.append("subscription_upsert")
            return subscription

    class Session:
        async def commit(self) -> None:
            events.append("transaction_commit")

        async def rollback(self) -> None:
            events.append("transaction_rollback")

    service = LocalBillingService.__new__(LocalBillingService)
    service.session = Session()
    service.usage_accounting_service = Usage()
    service.user_repository = Users()
    service.subscription_repository = Subscriptions()

    result = await service.activate_starter(user_id, now=FIXED_NOW)

    assert result is subscription
    assert events == [
        "grant_advisory_lock",
        "user_lock",
        "subscription_lock",
        "subscription_upsert",
        "usage_grant",
        "transaction_commit",
    ]
