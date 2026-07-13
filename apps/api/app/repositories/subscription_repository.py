from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.subscription import Subscription
from app.models.user import User


class StripeSubscriptionOwnershipError(ValueError):
    pass


class SubscriptionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_user_id(self, user_id) -> Subscription | None:
        result = await self.session.execute(select(Subscription).where(Subscription.user_id == user_id))
        return result.scalar_one_or_none()

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
        locked_user_id = await self.session.scalar(
            self._user_lock_statement(user_id)
        )
        if locked_user_id is None:
            raise ValueError("Subscription user does not exist")

        stripe_result = await self.session.execute(
            select(Subscription)
            .where(Subscription.stripe_subscription_id == stripe_subscription_id)
            .with_for_update()
        )
        subscription = stripe_result.scalar_one_or_none()
        if subscription is not None and subscription.user_id != user_id:
            raise StripeSubscriptionOwnershipError(
                "Stripe subscription is already assigned to another user"
            )

        if subscription is None:
            user_result = await self.session.execute(
                select(Subscription)
                .where(Subscription.user_id == user_id)
                .with_for_update()
            )
            subscription = user_result.scalar_one_or_none()

        if subscription is None:
            subscription = Subscription(user_id=user_id)
            self.session.add(subscription)

        subscription.stripe_customer_id = stripe_customer_id
        subscription.stripe_subscription_id = stripe_subscription_id
        subscription.plan_tier = plan_tier
        subscription.status = status
        subscription.allocated_minutes = allocated_minutes
        subscription.current_period_start = current_period_start
        subscription.current_period_end = current_period_end

        await self.session.flush()
        return subscription

    @staticmethod
    def _user_lock_statement(user_id):
        return select(User.id).where(User.id == user_id).with_for_update()

    async def get_by_stripe_subscription_id(self, stripe_subscription_id: str) -> Subscription | None:
        result = await self.session.execute(
            select(Subscription).where(Subscription.stripe_subscription_id == stripe_subscription_id)
        )
        return result.scalar_one_or_none()
