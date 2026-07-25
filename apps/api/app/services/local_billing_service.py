from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.subscription import Subscription
from app.repositories.account_deactivation_repository import (
    AccountDeactivationRepository,
)
from app.repositories.phone_number_repository import PhoneNumberRepository
from app.repositories.subscription_repository import SubscriptionRepository
from app.repositories.user_repository import UserRepository
from app.services.subscription_access_policy import SubscriptionAccessPolicy
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
        self.account_deactivation_repository = AccountDeactivationRepository(session)
        self.phone_number_repository = PhoneNumberRepository(session)
        self.subscription_repository = SubscriptionRepository(session)
        self.usage_accounting_service = UsageAccountingService(session)

    async def activate_starter(
        self,
        user_id: UUID,
        now: datetime,
    ) -> Subscription:
        activated_at = self._as_utc(now)
        customer_id = f"local_customer_{user_id}"

        try:
            observed_user = await self.user_repository.get_by_id(user_id)
            if observed_user is None:
                raise LocalBillingConflictError("user_unavailable")
            observed_generation = observed_user.lifecycle_generation
            invoice_id = f"local_invoice_{user_id}_g{observed_generation}"
            # This follows the grant subsystem's documented global order:
            # grant advisory lock, user row, subscription row, then usage grant.
            await self.usage_accounting_service.acquire_invoice_grant_lock(
                invoice_id=invoice_id
            )
            user = await self.user_repository.get_by_id_for_update(user_id)
            if user is None:
                raise LocalBillingConflictError("user_unavailable")
            if user.lifecycle_generation != observed_generation:
                raise LocalBillingConflictError("lifecycle_generation_changed")
            lifecycle_generation = user.lifecycle_generation
            subscription_id = f"local_subscription_{user_id}_g{lifecycle_generation}"
            incomplete_operation = await self.account_deactivation_repository.get_incomplete_by_user_id_for_update(
                user_id
            )

            subscription = await self.subscription_repository.get_by_user_id_for_update(
                user_id
            )
            is_expected_subscription = bool(
                subscription is not None
                and self._is_expected_local_subscription(
                    subscription,
                    customer_id=customer_id,
                    subscription_id=subscription_id,
                    lifecycle_generation=lifecycle_generation,
                )
            )
            if not is_expected_subscription:
                if subscription is not None and not (
                    subscription.stripe_subscription_id or ""
                ).startswith(f"local_subscription_{user_id}_g"):
                    raise LocalBillingConflictError("real_subscription_present")
                phone_number = (
                    await self.phone_number_repository.get_by_user_id_for_update(
                        user_id
                    )
                )
                if not SubscriptionAccessPolicy.can_start_checkout(
                    account_status=user.status,
                    subscription_status=(
                        subscription.status if subscription is not None else None
                    ),
                    has_incomplete_deactivation=incomplete_operation is not None,
                    has_phone=phone_number is not None,
                ):
                    raise LocalBillingConflictError("local_subscription_unavailable")
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
                        lifecycle_generation=lifecycle_generation,
                    )
                )
                if subscription is None:
                    raise LocalBillingConflictError("local_subscription_unavailable")
            assert subscription is not None
            if user.status == "inactive":
                await self.user_repository.reactivate(
                    user,
                    lifecycle_generation=subscription.lifecycle_generation,
                )

            await self.usage_accounting_service.grant_invoice(
                user_id=user_id,
                invoice_id=invoice_id,
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
        lifecycle_generation: int,
    ) -> bool:
        return bool(
            subscription.stripe_customer_id == customer_id
            and subscription.stripe_subscription_id == subscription_id
            and subscription.lifecycle_generation == lifecycle_generation
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
