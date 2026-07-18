from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.subscription import Subscription
from app.repositories.subscription_repository import SubscriptionRepository
from app.repositories.user_repository import UserRepository
from app.services.usage_accounting_service import UsageAccountingService


LOCAL_STARTER_MINUTES = 60
LOCAL_STARTER_PERIOD = timedelta(days=30)


class LocalBillingConflictError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class LocalBillingService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.user_repository = UserRepository(session)
        self.subscription_repository = SubscriptionRepository(session)
        self.usage_accounting_service = UsageAccountingService(session)

    async def activate_starter(
        self,
        user_id: UUID,
        now: datetime,
    ) -> Subscription:
        activated_at = self._as_utc(now)
        customer_id = f"local_customer_{user_id}"
        subscription_id = f"local_subscription_{user_id}"
        grant_source = f"local-starter:{user_id}"

        try:
            # This follows the grant subsystem's documented global order:
            # grant advisory lock, user row, subscription row, then usage grant.
            await self.usage_accounting_service.acquire_invoice_grant_lock(
                invoice_id=grant_source
            )
            user = await self.user_repository.get_by_id_for_update(user_id)
            if user is None:
                raise LocalBillingConflictError("user_unavailable")

            subscription = await self.subscription_repository.get_by_user_id_for_update(
                user_id
            )
            if subscription is None:
                subscription = (
                    await self.subscription_repository.upsert_by_stripe_subscription_id(
                        user_id=user_id,
                        stripe_customer_id=customer_id,
                        stripe_subscription_id=subscription_id,
                        plan_tier="starter",
                        status="active",
                        allocated_minutes=LOCAL_STARTER_MINUTES,
                        current_period_start=activated_at,
                        current_period_end=activated_at + LOCAL_STARTER_PERIOD,
                        stripe_subscription_created_at=activated_at,
                        last_stripe_event_created_at=activated_at,
                    )
                )
                if subscription is None:
                    raise LocalBillingConflictError("local_subscription_unavailable")
            elif not self._is_expected_local_subscription(
                subscription,
                customer_id=customer_id,
                subscription_id=subscription_id,
            ):
                raise LocalBillingConflictError("real_subscription_present")

            await self.usage_accounting_service.grant_invoice(
                user_id=user_id,
                invoice_id=grant_source,
                minutes=LOCAL_STARTER_MINUTES,
            )
            await self.session.commit()
            return subscription
        except Exception:
            await self.session.rollback()
            raise

    @staticmethod
    def _is_expected_local_subscription(
        subscription: Subscription,
        *,
        customer_id: str,
        subscription_id: str,
    ) -> bool:
        return bool(
            subscription.stripe_customer_id == customer_id
            and subscription.stripe_subscription_id == subscription_id
            and subscription.plan_tier == "starter"
            and subscription.status == "active"
            and subscription.allocated_minutes == LOCAL_STARTER_MINUTES
            and subscription.current_period_start is not None
            and subscription.current_period_end is not None
        )

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
