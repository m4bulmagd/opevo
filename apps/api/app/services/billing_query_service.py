from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.account_deactivation_repository import (
    AccountDeactivationRepository,
)
from app.repositories.billing_checkout_attempt_repository import (
    BillingCheckoutAttemptRepository,
)
from app.repositories.phone_number_repository import PhoneNumberRepository
from app.repositories.phone_number_provisioning_repository import (
    PhoneNumberProvisioningRepository,
)
from app.repositories.provider_cleanup_repository import ProviderCleanupRepository
from app.repositories.subscription_repository import SubscriptionRepository
from app.repositories.usage_repository import UsageRepository
from app.repositories.user_repository import UserRepository
from app.schemas.billing_api import (
    SubscriptionResponse,
    UsageLedgerEntryResponse,
    UsageLedgerListResponse,
    UsageSnapshotResponse,
)
from app.services.subscription_access_policy import SubscriptionAccessPolicy
from app.services.provider_work_policy import unresolved_provider_work_blocker


@dataclass(frozen=True)
class CheckoutEligibility:
    allowed: bool
    lifecycle_generation: int


@dataclass(frozen=True)
class CheckoutAttemptPreparation:
    allowed: bool
    lifecycle_generation: int
    attempt_id: UUID | None
    idempotency_key: str | None
    existing_session_id: str | None
    stripe_customer_id: str | None


class BillingQueryService:
    def __init__(
        self,
        session: AsyncSession | None = None,
        *,
        subscription_repository: SubscriptionRepository | None = None,
        usage_repository: UsageRepository | None = None,
        user_repository: UserRepository | None = None,
        account_deactivation_repository: AccountDeactivationRepository | None = None,
        phone_number_repository: PhoneNumberRepository | None = None,
        phone_number_provisioning_repository: PhoneNumberProvisioningRepository
        | None = None,
        provider_cleanup_repository: ProviderCleanupRepository | None = None,
    ) -> None:
        if subscription_repository is None or usage_repository is None:
            if session is None:
                raise ValueError(
                    "session is required when repositories are not provided"
                )
            subscription_repository = subscription_repository or SubscriptionRepository(
                session
            )
            usage_repository = usage_repository or UsageRepository(session)

        self.subscription_repository = subscription_repository
        self.usage_repository = usage_repository
        self.session = session
        self.user_repository = user_repository or (
            UserRepository(session) if session is not None else None
        )
        self.account_deactivation_repository = account_deactivation_repository or (
            AccountDeactivationRepository(session) if session is not None else None
        )
        self.phone_number_repository = phone_number_repository or (
            PhoneNumberRepository(session) if session is not None else None
        )
        self.phone_number_provisioning_repository = (
            phone_number_provisioning_repository
            or (
                PhoneNumberProvisioningRepository(session)
                if session is not None
                else None
            )
        )
        self.provider_cleanup_repository = provider_cleanup_repository or (
            ProviderCleanupRepository(session) if session is not None else None
        )
        self.checkout_attempt_repository = (
            BillingCheckoutAttemptRepository(session) if session is not None else None
        )

    async def end_business_transaction(self) -> None:
        """Release the request transaction before hosted-provider I/O."""
        if self.session is not None and self.session.in_transaction():
            await self.session.rollback()

    async def get_subscription(self, user_id: UUID) -> SubscriptionResponse | None:
        subscription = await self.subscription_repository.get_by_user_id(user_id)
        if subscription is None:
            return None
        can_start_checkout = False
        if (
            self.user_repository is not None
            and self.account_deactivation_repository is not None
            and self.phone_number_repository is not None
            and self.phone_number_provisioning_repository is not None
            and self.provider_cleanup_repository is not None
        ):
            user = await self.user_repository.get_by_id(user_id)
            operation = (
                await self.account_deactivation_repository.get_latest_by_user_id(
                    user_id
                )
            )
            phone = await self.phone_number_repository.get_by_user_id(user_id)
            cleanup_operations = (
                await self.provider_cleanup_repository.list_incomplete_by_user_id(
                    user_id
                )
            )
            provisioning = (
                await self.phone_number_provisioning_repository.get_by_user_id(user_id)
            )
            if user is not None:
                can_start_checkout = (
                    SubscriptionAccessPolicy.can_start_checkout(
                        account_status=user.status,
                        subscription_status=subscription.status,
                        has_incomplete_deactivation=bool(
                            operation is not None and operation.completed_at is None
                        ),
                        has_phone=phone is not None,
                    )
                    and unresolved_provider_work_blocker(
                        cleanup_operations=cleanup_operations,
                        provisioning=provisioning,
                    )
                    is None
                )

        return SubscriptionResponse(
            plan_tier=subscription.plan_tier,
            status=subscription.status,
            allocated_minutes=subscription.allocated_minutes,
            current_period_start=subscription.current_period_start,
            current_period_end=subscription.current_period_end,
            stripe_customer_id=subscription.stripe_customer_id,
            stripe_subscription_id=subscription.stripe_subscription_id,
            can_start_checkout=can_start_checkout,
            cancel_at_period_end=subscription.cancel_at_period_end,
            cancellation_effective_at=subscription.cancellation_effective_at,
        )

    async def get_checkout_eligibility(
        self,
        user_id: UUID,
    ) -> CheckoutEligibility:
        if (
            self.user_repository is None
            or self.account_deactivation_repository is None
            or self.phone_number_repository is None
            or self.phone_number_provisioning_repository is None
            or self.provider_cleanup_repository is None
        ):
            raise ValueError("session-backed repositories are required for checkout")
        user = await self.user_repository.get_by_id_for_update(user_id)
        if user is None:
            raise ValueError("Checkout user does not exist")
        operation = await self.account_deactivation_repository.get_incomplete_by_user_id_for_update(
            user_id
        )
        subscription = await self.subscription_repository.get_by_user_id_for_update(
            user_id
        )
        phone = await self.phone_number_repository.get_by_user_id_for_update(user_id)
        cleanup_operations = await self.provider_cleanup_repository.list_incomplete_by_user_id_for_update(
            user_id
        )
        provisioning = (
            await self.phone_number_provisioning_repository.get_by_user_id_for_update(
                user_id
            )
        )
        return CheckoutEligibility(
            allowed=SubscriptionAccessPolicy.can_start_checkout(
                account_status=user.status,
                subscription_status=(
                    subscription.status if subscription is not None else None
                ),
                has_incomplete_deactivation=operation is not None,
                has_phone=phone is not None,
            )
            and unresolved_provider_work_blocker(
                cleanup_operations=cleanup_operations,
                provisioning=provisioning,
            )
            is None,
            lifecycle_generation=user.lifecycle_generation,
        )

    async def prepare_checkout_attempt(
        self,
        user_id: UUID,
    ) -> CheckoutAttemptPreparation:
        if self.session is None or self.checkout_attempt_repository is None:
            raise ValueError("session-backed repositories are required for checkout")
        eligibility = await self.get_checkout_eligibility(user_id)
        subscription = await self.subscription_repository.get_by_user_id_for_update(
            user_id
        )
        if not eligibility.allowed:
            await self.session.rollback()
            return CheckoutAttemptPreparation(
                allowed=False,
                lifecycle_generation=eligibility.lifecycle_generation,
                attempt_id=None,
                idempotency_key=None,
                existing_session_id=None,
                stripe_customer_id=None,
            )
        attempt = await self.checkout_attempt_repository.get_or_create(
            user_id=user_id,
            lifecycle_generation=eligibility.lifecycle_generation,
        )
        preparation = CheckoutAttemptPreparation(
            allowed=True,
            lifecycle_generation=eligibility.lifecycle_generation,
            attempt_id=attempt.id,
            idempotency_key=attempt.idempotency_key,
            existing_session_id=attempt.stripe_checkout_session_id,
            stripe_customer_id=(
                subscription.stripe_customer_id if subscription is not None else None
            ),
        )
        await self.session.commit()
        return preparation

    async def complete_checkout_attempt(
        self,
        *,
        attempt_id: UUID,
        stripe_checkout_session_id: str,
    ) -> None:
        if self.session is None or self.checkout_attempt_repository is None:
            raise ValueError("session-backed repositories are required for checkout")
        try:
            await self.checkout_attempt_repository.complete(
                attempt_id=attempt_id,
                stripe_checkout_session_id=stripe_checkout_session_id,
            )
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            raise

    async def get_usage_snapshot(self, user_id: UUID | str) -> UsageSnapshotResponse:
        subscription = await self.subscription_repository.get_by_user_id(user_id)
        minutes_remaining = await self.usage_repository.get_current_balance(
            user_id=user_id
        )

        if subscription is None:
            return UsageSnapshotResponse(
                minutes_remaining=minutes_remaining,
                allocated_minutes=0,
                plan_tier=None,
                subscription_status=None,
                current_period_start=None,
                current_period_end=None,
            )

        return UsageSnapshotResponse(
            minutes_remaining=minutes_remaining,
            allocated_minutes=subscription.allocated_minutes,
            plan_tier=subscription.plan_tier,
            subscription_status=subscription.status,
            current_period_start=subscription.current_period_start,
            current_period_end=subscription.current_period_end,
        )

    async def get_usage_ledger(
        self, user_id: UUID | str, *, limit: int
    ) -> UsageLedgerListResponse:
        entries = await self.usage_repository.list_recent_by_user_id(
            user_id=user_id, limit=limit
        )
        return UsageLedgerListResponse(
            entries=[
                UsageLedgerEntryResponse(
                    id=str(entry.id),
                    event_type=entry.event_type,
                    minutes_delta=entry.minutes_delta,
                    balance_after=entry.balance_after,
                    call_id=str(entry.call_id) if entry.call_id is not None else None,
                    created_at=entry.created_at,
                )
                for entry in entries
            ]
        )
