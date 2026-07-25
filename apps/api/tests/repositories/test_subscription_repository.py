from datetime import UTC, datetime

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.subscription import Subscription
from app.models.user import User
from app.repositories.subscription_repository import (
    StripeSubscriptionConflictError,
    StripeSubscriptionOwnershipError,
    SubscriptionRepository,
)


async def _user(db_session: AsyncSession, suffix: str) -> User:
    user = User(
        clerk_user_id=f"user_{suffix}",
        email=f"{suffix}@example.com",
    )
    db_session.add(user)
    await db_session.flush()
    return user


def _upsert_arguments(
    user_id,
    *,
    subscription_id: str,
    customer_id: str,
    status: str = "active",
    lifecycle_generation: int = 1,
    subscription_created_at: datetime | None = None,
    event_created_at: datetime | None = None,
    cancel_at_period_end: bool = False,
    cancellation_effective_at: datetime | None = None,
) -> dict:
    return {
        "user_id": user_id,
        "stripe_customer_id": customer_id,
        "stripe_subscription_id": subscription_id,
        "plan_tier": "starter",
        "status": status,
        "allocated_minutes": 60,
        "current_period_start": None,
        "current_period_end": None,
        "stripe_subscription_created_at": subscription_created_at,
        "last_stripe_event_created_at": event_created_at,
        "lifecycle_generation": lifecycle_generation,
        "cancel_at_period_end": cancel_at_period_end,
        "cancellation_effective_at": cancellation_effective_at,
    }


@pytest.mark.anyio
async def test_resubscription_updates_the_existing_user_row(
    db_session: AsyncSession,
) -> None:
    user = await _user(db_session, "resubscribe")
    repository = SubscriptionRepository(db_session)
    original = await repository.upsert_by_stripe_subscription_id(
        **_upsert_arguments(
            user.id,
            subscription_id="sub_old",
            customer_id="cus_resubscribe",
            status="canceled",
            subscription_created_at=datetime(2026, 1, 1, tzinfo=UTC),
            event_created_at=datetime(2026, 2, 1, tzinfo=UTC),
        )
    )

    replacement = await repository.upsert_by_stripe_subscription_id(
        **_upsert_arguments(
            user.id,
            subscription_id="sub_new",
            customer_id="cus_resubscribe",
            subscription_created_at=datetime(2026, 3, 1, tzinfo=UTC),
            event_created_at=datetime(2026, 3, 1, tzinfo=UTC),
        )
    )

    assert original is not None
    assert replacement is not None
    assert replacement.id == original.id
    assert replacement.stripe_subscription_id == "sub_new"
    assert await db_session.scalar(select(func.count()).select_from(Subscription)) == 1


@pytest.mark.anyio
async def test_upsert_persists_and_reverses_scheduled_cancellation(
    db_session: AsyncSession,
) -> None:
    user = await _user(db_session, "scheduled-cancellation")
    repository = SubscriptionRepository(db_session)
    effective_at = datetime(2026, 4, 1, tzinfo=UTC)

    scheduled = await repository.upsert_by_stripe_subscription_id(
        **_upsert_arguments(
            user.id,
            subscription_id="sub_scheduled",
            customer_id="cus_scheduled",
            event_created_at=datetime(2026, 3, 1, tzinfo=UTC),
            cancel_at_period_end=True,
            cancellation_effective_at=effective_at,
        )
    )
    reversed_schedule = await repository.upsert_by_stripe_subscription_id(
        **_upsert_arguments(
            user.id,
            subscription_id="sub_scheduled",
            customer_id="cus_scheduled",
            event_created_at=datetime(2026, 3, 2, tzinfo=UTC),
        )
    )

    assert scheduled is not None
    assert reversed_schedule is not None
    assert scheduled is reversed_schedule
    assert reversed_schedule.cancel_at_period_end is False
    assert reversed_schedule.cancellation_effective_at is None


@pytest.mark.anyio
async def test_old_lifecycle_generation_cannot_replace_current_subscription(
    db_session: AsyncSession,
) -> None:
    user = await _user(db_session, "lifecycle-generation")
    user.lifecycle_generation = 2
    repository = SubscriptionRepository(db_session)
    current = Subscription(
        user_id=user.id,
        stripe_customer_id="cus_generation_2",
        stripe_subscription_id="sub_generation_2",
        plan_tier="starter",
        status="active",
        allocated_minutes=60,
        lifecycle_generation=2,
        stripe_subscription_created_at=datetime(2026, 3, 1, tzinfo=UTC),
        last_stripe_event_created_at=datetime(2026, 3, 1, tzinfo=UTC),
    )
    db_session.add(current)
    await db_session.flush()

    ignored = await repository.upsert_by_stripe_subscription_id(
        **_upsert_arguments(
            user.id,
            subscription_id="sub_generation_1_late",
            customer_id="cus_generation_1",
            status="canceled",
            lifecycle_generation=1,
            subscription_created_at=datetime(2026, 4, 1, tzinfo=UTC),
            event_created_at=datetime(2026, 4, 1, tzinfo=UTC),
        )
    )

    assert current is not None
    assert ignored is None
    assert current.stripe_subscription_id == "sub_generation_2"
    assert current.status == "active"


@pytest.mark.anyio
async def test_older_subscription_generation_cannot_replace_current_row(
    db_session: AsyncSession,
) -> None:
    user = await _user(db_session, "old-generation")
    repository = SubscriptionRepository(db_session)
    current = await repository.upsert_by_stripe_subscription_id(
        **_upsert_arguments(
            user.id,
            subscription_id="sub_new",
            customer_id="cus_generation",
            subscription_created_at=datetime(2026, 3, 1, tzinfo=UTC),
            event_created_at=datetime(2026, 3, 1, tzinfo=UTC),
        )
    )

    ignored = await repository.upsert_by_stripe_subscription_id(
        **_upsert_arguments(
            user.id,
            subscription_id="sub_old",
            customer_id="cus_generation",
            status="canceled",
            subscription_created_at=datetime(2026, 1, 1, tzinfo=UTC),
            event_created_at=datetime(2026, 4, 1, tzinfo=UTC),
        )
    )

    assert current is not None
    assert ignored is None
    assert current.stripe_subscription_id == "sub_new"
    assert current.status == "active"


@pytest.mark.anyio
async def test_legacy_same_id_delayed_routing_event_cannot_restore_access(
    db_session: AsyncSession,
) -> None:
    user = await _user(db_session, "legacy-delayed-routing")
    legacy_watermark = datetime(2026, 4, 1, tzinfo=UTC)
    legacy = Subscription(
        user_id=user.id,
        stripe_customer_id="cus_legacy_delayed",
        stripe_subscription_id="sub_legacy_delayed",
        plan_tier="starter",
        status="canceled",
        allocated_minutes=60,
        stripe_subscription_created_at=None,
        last_stripe_event_created_at=None,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        updated_at=legacy_watermark,
    )
    db_session.add(legacy)
    await db_session.flush()

    ignored = await SubscriptionRepository(db_session).upsert_by_stripe_subscription_id(
        **_upsert_arguments(
            user.id,
            subscription_id="sub_legacy_delayed",
            customer_id="cus_legacy_delayed",
            status="active",
            subscription_created_at=datetime(2026, 1, 1, tzinfo=UTC),
            event_created_at=datetime(2026, 3, 1, tzinfo=UTC),
        )
    )

    assert ignored is None
    assert legacy.status == "canceled"
    assert legacy.stripe_subscription_created_at is None
    assert legacy.last_stripe_event_created_at is None


@pytest.mark.anyio
async def test_legacy_same_id_delayed_nonrouting_event_still_revokes_access(
    db_session: AsyncSession,
) -> None:
    user = await _user(db_session, "legacy-delayed-revocation")
    legacy = Subscription(
        user_id=user.id,
        stripe_customer_id="cus_legacy_revocation",
        stripe_subscription_id="sub_legacy_revocation",
        plan_tier="starter",
        status="active",
        allocated_minutes=60,
        stripe_subscription_created_at=None,
        last_stripe_event_created_at=None,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        updated_at=datetime(2026, 4, 1, tzinfo=UTC),
    )
    db_session.add(legacy)
    await db_session.flush()

    updated = await SubscriptionRepository(db_session).upsert_by_stripe_subscription_id(
        **_upsert_arguments(
            user.id,
            subscription_id="sub_legacy_revocation",
            customer_id="cus_legacy_revocation",
            status="past_due",
            subscription_created_at=datetime(2026, 1, 1, tzinfo=UTC),
            event_created_at=datetime(2026, 3, 1, tzinfo=UTC),
        )
    )

    assert updated is legacy
    assert legacy.status == "past_due"
    assert legacy.last_stripe_event_created_at is None


@pytest.mark.anyio
async def test_older_subscription_cannot_replace_terminal_legacy_row(
    db_session: AsyncSession,
) -> None:
    user = await _user(db_session, "legacy-old-replacement")
    legacy = Subscription(
        user_id=user.id,
        stripe_customer_id="cus_legacy_replacement",
        stripe_subscription_id="sub_legacy_current",
        plan_tier="starter",
        status="canceled",
        allocated_minutes=60,
        stripe_subscription_created_at=None,
        last_stripe_event_created_at=None,
        created_at=datetime(2026, 4, 1, tzinfo=UTC),
        updated_at=datetime(2026, 4, 1, tzinfo=UTC),
    )
    db_session.add(legacy)
    await db_session.flush()

    ignored = await SubscriptionRepository(db_session).upsert_by_stripe_subscription_id(
        **_upsert_arguments(
            user.id,
            subscription_id="sub_legacy_older",
            customer_id="cus_legacy_replacement",
            status="active",
            subscription_created_at=datetime(2026, 3, 1, tzinfo=UTC),
            event_created_at=datetime(2026, 5, 1, tzinfo=UTC),
        )
    )

    assert ignored is None
    assert legacy.stripe_subscription_id == "sub_legacy_current"
    assert legacy.status == "canceled"


@pytest.mark.anyio
async def test_equal_generation_for_distinct_subscription_is_ambiguous(
    db_session: AsyncSession,
) -> None:
    user = await _user(db_session, "equal-generation")
    generation = datetime(2026, 3, 1, tzinfo=UTC)
    await SubscriptionRepository(db_session).upsert_by_stripe_subscription_id(
        **_upsert_arguments(
            user.id,
            subscription_id="sub_equal_current",
            customer_id="cus_equal_generation",
            status="canceled",
            subscription_created_at=generation,
            event_created_at=datetime(2026, 4, 1, tzinfo=UTC),
        )
    )

    with pytest.raises(StripeSubscriptionConflictError):
        await SubscriptionRepository(db_session).upsert_by_stripe_subscription_id(
            **_upsert_arguments(
                user.id,
                subscription_id="sub_equal_other",
                customer_id="cus_equal_generation",
                status="active",
                subscription_created_at=generation,
                event_created_at=datetime(2026, 5, 1, tzinfo=UTC),
            )
        )


@pytest.mark.anyio
async def test_legacy_invoice_uses_effective_generation_and_event_watermark(
    db_session: AsyncSession,
) -> None:
    user = await _user(db_session, "legacy-invoice")
    legacy = Subscription(
        user_id=user.id,
        stripe_customer_id="cus_legacy_invoice",
        stripe_subscription_id="sub_legacy_invoice",
        plan_tier="starter",
        status="canceled",
        allocated_minutes=60,
        stripe_subscription_created_at=None,
        last_stripe_event_created_at=None,
        created_at=datetime(2026, 3, 1, tzinfo=UTC),
        updated_at=datetime(2026, 4, 1, tzinfo=UTC),
    )
    db_session.add(legacy)
    await db_session.flush()
    repository = SubscriptionRepository(db_session)

    same_id, apply_same_id = await repository.resolve_invoice_target_for_update(
        stripe_subscription_id="sub_legacy_invoice",
        incoming_status="active",
        event_created_at=datetime(2026, 3, 15, tzinfo=UTC),
    )
    old_id, apply_old_id = await repository.resolve_invoice_target_for_update(
        stripe_subscription_id="sub_legacy_older",
        user_id=user.id,
        incoming_status="active",
        event_created_at=datetime(2026, 2, 1, tzinfo=UTC),
    )

    assert same_id is legacy
    assert apply_same_id is False
    assert old_id is legacy
    assert apply_old_id is False


@pytest.mark.anyio
async def test_newer_subscription_generation_cannot_replace_nonterminal_row(
    db_session: AsyncSession,
) -> None:
    from app.repositories.subscription_repository import StripeSubscriptionConflictError

    user = await _user(db_session, "nonterminal-generation")
    repository = SubscriptionRepository(db_session)
    await repository.upsert_by_stripe_subscription_id(
        **_upsert_arguments(
            user.id,
            subscription_id="sub_current",
            customer_id="cus_nonterminal",
            subscription_created_at=datetime(2026, 1, 1, tzinfo=UTC),
            event_created_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
    )

    with pytest.raises(StripeSubscriptionConflictError):
        await repository.upsert_by_stripe_subscription_id(
            **_upsert_arguments(
                user.id,
                subscription_id="sub_new",
                customer_id="cus_nonterminal",
                subscription_created_at=datetime(2026, 2, 1, tzinfo=UTC),
                event_created_at=datetime(2026, 2, 1, tzinfo=UTC),
            )
        )


@pytest.mark.anyio
async def test_stripe_subscription_cannot_be_reassigned_to_another_user(
    db_session: AsyncSession,
) -> None:
    owner = await _user(db_session, "owner")
    attacker = await _user(db_session, "attacker")
    repository = SubscriptionRepository(db_session)
    owned = await repository.upsert_by_stripe_subscription_id(
        **_upsert_arguments(
            owner.id,
            subscription_id="sub_owned",
            customer_id="cus_owner",
        )
    )
    assert owned is not None

    with pytest.raises(StripeSubscriptionOwnershipError):
        await repository.upsert_by_stripe_subscription_id(
            **_upsert_arguments(
                attacker.id,
                subscription_id="sub_owned",
                customer_id="cus_attacker",
            )
        )

    await db_session.refresh(owned)
    assert owned.user_id == owner.id
    assert await db_session.scalar(select(func.count()).select_from(Subscription)) == 1


def test_subscription_upsert_locks_the_user_accounting_scope() -> None:
    statement = SubscriptionRepository._user_lock_statement("user-id")

    compiled = str(statement.compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE" in compiled
