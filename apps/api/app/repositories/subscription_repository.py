from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.subscription import Subscription


class SubscriptionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def upsert_by_stripe_subscription_id(
        self,
        *,
        user_id,
        stripe_customer_id: str,
        stripe_subscription_id: str,
        plan_tier: str,
        status: str,
        allocated_minutes: int,
        current_period_start,
        current_period_end,
    ) -> Subscription:
        result = await self.session.execute(
            select(Subscription).where(Subscription.stripe_subscription_id == stripe_subscription_id)
        )
        subscription = result.scalar_one_or_none()
        if subscription is None:
            subscription = Subscription(
                user_id=user_id,
                stripe_customer_id=stripe_customer_id,
                stripe_subscription_id=stripe_subscription_id,
                plan_tier=plan_tier,
                status=status,
                allocated_minutes=allocated_minutes,
                current_period_start=current_period_start,
                current_period_end=current_period_end,
            )
            self.session.add(subscription)
        else:
            subscription.user_id = user_id
            subscription.stripe_customer_id = stripe_customer_id
            subscription.plan_tier = plan_tier
            subscription.status = status
            subscription.allocated_minutes = allocated_minutes
            subscription.current_period_start = current_period_start
            subscription.current_period_end = current_period_end

        await self.session.flush()
        return subscription

    async def get_by_stripe_subscription_id(self, stripe_subscription_id: str) -> Subscription | None:
        result = await self.session.execute(
            select(Subscription).where(Subscription.stripe_subscription_id == stripe_subscription_id)
        )
        return result.scalar_one_or_none()
