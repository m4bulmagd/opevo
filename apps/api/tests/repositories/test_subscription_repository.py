from datetime import UTC, datetime

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.subscription import Subscription
from app.models.user import User
from app.repositories.subscription_repository import (
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
    subscription_created_at: datetime | None = None,
    event_created_at: datetime | None = None,
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

    assert replacement.id == original.id
    assert replacement.stripe_subscription_id == "sub_new"
    assert await db_session.scalar(select(func.count()).select_from(Subscription)) == 1


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

    assert ignored is None
    assert current.stripe_subscription_id == "sub_new"
    assert current.status == "active"


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
