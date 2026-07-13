from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.subscription import Subscription
from app.models.user import User
from app.services.subscription_access_policy import SubscriptionAccessPolicy


class StripeSubscriptionOwnershipError(ValueError):
    pass


class StripeSubscriptionConflictError(ValueError):
    pass


class StripeSubscriptionDataError(ValueError):
    pass


class SubscriptionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_user_id(self, user_id) -> Subscription | None:
        result = await self.session.execute(select(Subscription).where(Subscription.user_id == user_id))
        return result.scalar_one_or_none()

    async def get_user_id_by_stripe_subscription_id(
        self,
        stripe_subscription_id: str,
    ) -> UUID | None:
        return await self.session.scalar(
            select(Subscription.user_id).where(
                Subscription.stripe_subscription_id == stripe_subscription_id
            )
        )

    async def upsert_by_stripe_subscription_id(
        self,
        *,
        user_id,
        stripe_customer_id: str | None,
        stripe_subscription_id: str,
        plan_tier: str | None,
        status: str,
        allocated_minutes: int | None,
        current_period_start,
        current_period_end,
        stripe_subscription_created_at: datetime | None = None,
        last_stripe_event_created_at: datetime | None = None,
    ) -> Subscription | None:
        locked_user_id = await self.session.scalar(
            self._user_lock_statement(user_id)
        )
        if locked_user_id is None:
            raise ValueError("Subscription user does not exist")

        current_result = await self.session.execute(
            select(Subscription)
            .where(Subscription.user_id == user_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        subscription = current_result.scalar_one_or_none()

        target_user_id = await self.session.scalar(
            select(Subscription.user_id)
            .where(Subscription.stripe_subscription_id == stripe_subscription_id)
            .with_for_update()
        )
        if target_user_id is not None and target_user_id != user_id:
            raise StripeSubscriptionOwnershipError(
                "Stripe subscription is already assigned to another user"
            )

        is_same_subscription = bool(
            subscription is not None
            and subscription.stripe_subscription_id == stripe_subscription_id
        )
        preserve_unknown_event_watermark = bool(
            is_same_subscription
            and subscription is not None
            and subscription.last_stripe_event_created_at is None
        )

        if subscription is not None:
            if is_same_subscription:
                if self._same_subscription_event_is_stale(
                    subscription,
                    incoming_status=status,
                    event_created_at=last_stripe_event_created_at,
                ):
                    return None
                if self._same_id_generation_conflicts(
                    subscription,
                    stripe_subscription_created_at,
                ):
                    raise StripeSubscriptionConflictError(
                        "Stripe subscription generation changed for the same ID"
                    )
            elif self._different_subscription_event_is_obsolete(
                subscription,
                stripe_subscription_created_at,
            ):
                return None
            elif stripe_subscription_created_at is None:
                raise StripeSubscriptionConflictError(
                    "A replacement subscription must have a known generation"
                )
            elif self._different_subscription_generation_is_ambiguous(
                subscription,
                stripe_subscription_created_at,
            ):
                raise StripeSubscriptionConflictError(
                    "Distinct Stripe subscriptions have an ambiguous generation"
                )
            elif not SubscriptionAccessPolicy.can_replace_subscription(
                subscription.status
            ):
                raise StripeSubscriptionConflictError(
                    "A nonterminal subscription cannot be replaced"
                )

        if subscription is None:
            subscription = Subscription(user_id=user_id)
            self.session.add(subscription)

        if is_same_subscription:
            resolved_customer_id = (
                stripe_customer_id or subscription.stripe_customer_id
            )
            resolved_plan_tier = plan_tier or subscription.plan_tier
            resolved_allocated_minutes = (
                allocated_minutes
                if allocated_minutes is not None
                else subscription.allocated_minutes
            )
        else:
            resolved_customer_id = stripe_customer_id
            resolved_plan_tier = plan_tier
            resolved_allocated_minutes = allocated_minutes

        if (
            not resolved_customer_id
            or not resolved_plan_tier
            or resolved_allocated_minutes is None
        ):
            raise StripeSubscriptionDataError(
                "Stripe subscription is missing required customer or plan data"
            )

        subscription.stripe_customer_id = resolved_customer_id
        subscription.stripe_subscription_id = stripe_subscription_id
        subscription.plan_tier = resolved_plan_tier
        subscription.status = status
        subscription.allocated_minutes = resolved_allocated_minutes
        if not is_same_subscription or current_period_start is not None:
            subscription.current_period_start = current_period_start
        if not is_same_subscription or current_period_end is not None:
            subscription.current_period_end = current_period_end
        if stripe_subscription_created_at is not None:
            subscription.stripe_subscription_created_at = stripe_subscription_created_at
        if (
            last_stripe_event_created_at is not None
            and not preserve_unknown_event_watermark
        ):
            subscription.last_stripe_event_created_at = last_stripe_event_created_at

        await self.session.flush()
        return subscription

    async def resolve_invoice_target_for_update(
        self,
        *,
        stripe_subscription_id: str,
        user_id=None,
        incoming_status: str,
        event_created_at: datetime,
    ) -> tuple[Subscription | None, bool]:
        resolved_user_id = user_id
        can_bootstrap = user_id is not None
        if resolved_user_id is None:
            resolved_user_id = await self.session.scalar(
                select(Subscription.user_id).where(
                    Subscription.stripe_subscription_id == stripe_subscription_id
                )
            )
            if resolved_user_id is None:
                return None, False

        locked_user_id = await self.session.scalar(
            self._user_lock_statement(resolved_user_id)
        )
        if locked_user_id is None:
            return None, False

        current = await self.session.scalar(
            select(Subscription)
            .where(Subscription.user_id == resolved_user_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if current is None:
            return None, can_bootstrap

        if current.stripe_subscription_id != stripe_subscription_id:
            current_generation = self._effective_subscription_generation(current)
            if self._as_utc(event_created_at) < self._as_utc(current_generation):
                return current, False
            raise StripeSubscriptionConflictError(
                "Invoice does not match the current Stripe subscription"
            )

        if self._same_subscription_event_is_stale(
            current,
            incoming_status=incoming_status,
            event_created_at=event_created_at,
        ):
            return current, False
        return current, True

    @staticmethod
    def _user_lock_statement(user_id):
        return select(User.id).where(User.id == user_id).with_for_update()

    async def get_by_stripe_subscription_id(self, stripe_subscription_id: str) -> Subscription | None:
        result = await self.session.execute(
            select(Subscription).where(Subscription.stripe_subscription_id == stripe_subscription_id)
        )
        return result.scalar_one_or_none()

    @classmethod
    def _same_subscription_event_is_stale(
        cls,
        subscription: Subscription,
        *,
        incoming_status: str,
        event_created_at: datetime | None,
    ) -> bool:
        current_can_route = SubscriptionAccessPolicy.can_route(
            subscription.status,
            subscription.current_period_end,
        )
        incoming_can_route = SubscriptionAccessPolicy.can_route(
            incoming_status,
            None,
        )
        last_event_created_at = subscription.last_stripe_event_created_at
        if last_event_created_at is None:
            return incoming_can_route and not current_can_route
        if event_created_at is None:
            return False

        incoming_created_at = cls._as_utc(event_created_at)
        last_created_at = cls._as_utc(last_event_created_at)
        if incoming_created_at < last_created_at:
            return True
        return bool(
            incoming_created_at == last_created_at
            and incoming_can_route
            and not current_can_route
        )

    @classmethod
    def _different_subscription_event_is_obsolete(
        cls,
        subscription: Subscription,
        incoming_generation: datetime | None,
    ) -> bool:
        if incoming_generation is None:
            return False
        current_generation = cls._effective_subscription_generation(subscription)
        return bool(
            cls._as_utc(incoming_generation) < cls._as_utc(current_generation)
        )

    @classmethod
    def _different_subscription_generation_is_ambiguous(
        cls,
        subscription: Subscription,
        incoming_generation: datetime,
    ) -> bool:
        current_generation = cls._effective_subscription_generation(subscription)
        return cls._as_utc(incoming_generation) == cls._as_utc(current_generation)

    @classmethod
    def _same_id_generation_conflicts(
        cls,
        subscription: Subscription,
        incoming_generation: datetime | None,
    ) -> bool:
        current_generation = subscription.stripe_subscription_created_at
        return bool(
            current_generation is not None
            and incoming_generation is not None
            and cls._as_utc(incoming_generation) != cls._as_utc(current_generation)
        )

    @staticmethod
    def _effective_subscription_generation(subscription: Subscription) -> datetime:
        return subscription.stripe_subscription_created_at or subscription.created_at

    @staticmethod
    def advance_known_event_watermark(
        subscription: Subscription,
        event_created_at: datetime,
    ) -> None:
        if subscription.last_stripe_event_created_at is not None:
            subscription.last_stripe_event_created_at = event_created_at

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
