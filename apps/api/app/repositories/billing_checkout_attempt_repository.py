from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.billing_checkout_attempt import BillingCheckoutAttempt


class BillingCheckoutAttemptRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_or_create(
        self,
        *,
        user_id: UUID,
        lifecycle_generation: int,
    ) -> BillingCheckoutAttempt:
        attempt = await self.session.scalar(
            select(BillingCheckoutAttempt)
            .where(
                BillingCheckoutAttempt.user_id == user_id,
                BillingCheckoutAttempt.lifecycle_generation
                == lifecycle_generation,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if attempt is not None:
            return attempt
        attempt = BillingCheckoutAttempt(
            user_id=user_id,
            lifecycle_generation=lifecycle_generation,
            idempotency_key=f"billing.checkout:{user_id}:g{lifecycle_generation}",
            status="pending",
        )
        self.session.add(attempt)
        await self.session.flush()
        return attempt

    async def get_by_id_for_update(
        self,
        attempt_id: UUID,
    ) -> BillingCheckoutAttempt | None:
        return await self.session.scalar(
            select(BillingCheckoutAttempt)
            .where(BillingCheckoutAttempt.id == attempt_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    async def complete(
        self,
        *,
        attempt_id: UUID,
        stripe_checkout_session_id: str,
    ) -> BillingCheckoutAttempt:
        if not stripe_checkout_session_id:
            raise ValueError("Stripe checkout session identity is required")
        attempt = await self.get_by_id_for_update(attempt_id)
        if attempt is None:
            raise ValueError("Checkout attempt not found")
        if (
            attempt.stripe_checkout_session_id is not None
            and attempt.stripe_checkout_session_id != stripe_checkout_session_id
        ):
            raise ValueError("Stripe checkout session identity conflict")
        attempt.stripe_checkout_session_id = stripe_checkout_session_id
        attempt.status = "completed"
        await self.session.flush()
        return attempt
