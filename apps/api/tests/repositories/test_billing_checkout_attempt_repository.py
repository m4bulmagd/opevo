import pytest
from sqlalchemy import func, select

from app.models.billing_checkout_attempt import BillingCheckoutAttempt
from app.repositories.billing_checkout_attempt_repository import (
    BillingCheckoutAttemptRepository,
)


@pytest.mark.anyio
async def test_checkout_attempt_is_one_durable_identity_per_owner_generation(
    db_session,
    active_user,
) -> None:
    repository = BillingCheckoutAttemptRepository(db_session)

    first = await repository.get_or_create(
        user_id=active_user.id,
        lifecycle_generation=active_user.lifecycle_generation,
    )
    repeated = await repository.get_or_create(
        user_id=active_user.id,
        lifecycle_generation=active_user.lifecycle_generation,
    )

    assert repeated.id == first.id
    assert repeated.idempotency_key == first.idempotency_key
    assert repeated.status == "pending"
    assert (
        await db_session.scalar(
            select(func.count()).select_from(BillingCheckoutAttempt)
        )
        == 1
    )


@pytest.mark.anyio
async def test_checkout_attempt_rejects_conflicting_provider_session_identity(
    db_session,
    active_user,
) -> None:
    repository = BillingCheckoutAttemptRepository(db_session)
    attempt = await repository.get_or_create(
        user_id=active_user.id,
        lifecycle_generation=active_user.lifecycle_generation,
    )
    await repository.complete(
        attempt_id=attempt.id,
        stripe_checkout_session_id="cs_first",
    )

    with pytest.raises(ValueError, match="session identity conflict"):
        await repository.complete(
            attempt_id=attempt.id,
            stripe_checkout_session_id="cs_second",
        )
