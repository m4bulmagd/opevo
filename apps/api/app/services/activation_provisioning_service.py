from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.business_profile import BusinessProfile
from app.models.customer_activation import CustomerActivation
from app.models.subscription import Subscription
from app.repositories.activation_event_repository import ActivationEventRepository
from app.repositories.business_profile_repository import BusinessProfileRepository
from app.repositories.customer_activation_repository import (
    CustomerActivationRepository,
)
from app.repositories.phone_number_provisioning_repository import (
    PhoneNumberProvisioningRepository,
    ProvisioningStateConflictError,
)
from app.repositories.phone_number_repository import PhoneNumberRepository
from app.repositories.subscription_repository import SubscriptionRepository
from app.repositories.usage_repository import UsageRepository
from app.repositories.user_repository import UserRepository
from app.schemas.activation import ActivationSnapshotResponse
from app.services.activation_snapshot_service import ActivationSnapshotService
from app.services.business_profile_service import REQUIRED_PROFILE_FIELDS
from app.services.customer_readiness_policy import CustomerReadinessPolicy
from app.services.outbox_service import OutboxService


logger = logging.getLogger(__name__)


class ActivationProvisioningBlockedError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ActivationProvisioningService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        user_repository=None,
        activation_repository=None,
        business_profile_repository=None,
        subscription_repository=None,
        usage_repository=None,
        provisioning_repository=None,
        phone_number_repository=None,
        outbox_service=None,
        activation_event_repository=None,
        snapshot_service=None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.session = session
        self.user_repository = user_repository or UserRepository(session)
        self.activation_repository = (
            activation_repository or CustomerActivationRepository(session)
        )
        self.business_profile_repository = (
            business_profile_repository or BusinessProfileRepository(session)
        )
        self.subscription_repository = (
            subscription_repository or SubscriptionRepository(session)
        )
        self.usage_repository = usage_repository or UsageRepository(session)
        self.provisioning_repository = (
            provisioning_repository or PhoneNumberProvisioningRepository(session)
        )
        self.phone_number_repository = (
            phone_number_repository or PhoneNumberRepository(session)
        )
        self.outbox_service = outbox_service or OutboxService(session)
        self.activation_event_repository = (
            activation_event_repository or ActivationEventRepository(session)
        )
        self.snapshot_service = snapshot_service or ActivationSnapshotService(session)
        self.now = now or (lambda: datetime.now(UTC))

    async def confirm(
        self,
        user_id: UUID,
        *,
        arq_pool,
    ) -> ActivationSnapshotResponse:
        try:
            user = await self.user_repository.get_by_id_for_update(user_id)
            if user is None:
                raise ActivationProvisioningBlockedError("profile_unavailable")
            if user.status != "active":
                raise ActivationProvisioningBlockedError("user_inactive")
            activation = await self.activation_repository.get_by_user_id_for_update(
                user_id
            )
            profile = await self.business_profile_repository.get_by_user_id_for_update(
                user_id
            )
            self._require_current_confirmed_profile(
                activation=activation,
                profile=profile,
            )
            if user.country_code != "FR":
                raise ActivationProvisioningBlockedError("unsupported_country")

            subscription = (
                await self.subscription_repository.get_by_user_id_for_update(user_id)
            )
            balance = await self.usage_repository.get_current_balance(user_id=user_id)
            self._require_eligible_access(
                subscription=subscription,
                balance=balance,
                now=self.now(),
            )

            assert activation is not None
            operation_key = (
                activation.provisioning_idempotency_key
                or f"activation:phone.provision:{activation.id}"
            )
            already_consented = activation.provisioning_consented_at is not None
            provisioning = await self.provisioning_repository.queue_initial(
                user_id=user_id,
                operation_key=operation_key,
            )
            phone_number = await self.phone_number_repository.get_by_user_id_for_update(
                user_id
            )
            if phone_number is not None and not (
                already_consented
                and provisioning.status == "succeeded"
                and provisioning.phone_number_id == phone_number.id
            ):
                raise ActivationProvisioningBlockedError("phone_already_assigned")

            if activation.provisioning_idempotency_key is None:
                activation.provisioning_idempotency_key = operation_key
            elif activation.provisioning_idempotency_key != operation_key:
                raise ActivationProvisioningBlockedError(
                    "provisioning_state_conflict"
                )
            if activation.provisioning_consented_at is None:
                activation.provisioning_consented_at = self.now()

            await self.outbox_service.add(
                topic="phone.provision",
                aggregate_type="user",
                aggregate_id=user_id,
                idempotency_key=operation_key,
                payload={"user_id": str(user_id)},
            )
            await self.activation_event_repository.append(
                user_id=user_id,
                activation_id=activation.id,
                event_type="provisioning_consented",
                idempotency_key=f"activation-event:{operation_key}",
                metadata={"country_code": "FR"},
            )
            await self.session.commit()
        except ProvisioningStateConflictError:
            await self.session.rollback()
            raise ActivationProvisioningBlockedError(
                "provisioning_state_conflict"
            ) from None
        except Exception:
            await self.session.rollback()
            raise

        await self._wake_outbox(arq_pool, operation="confirm_phone_provisioning")
        return await self.snapshot_service.get(user_id)

    async def retry(
        self,
        user_id: UUID,
        *,
        arq_pool,
    ) -> ActivationSnapshotResponse:
        try:
            user = await self.user_repository.get_by_id_for_update(user_id)
            if user is None:
                raise ActivationProvisioningBlockedError("profile_unavailable")
            if user.status != "active":
                raise ActivationProvisioningBlockedError("user_inactive")
            activation = await self.activation_repository.get_by_user_id_for_update(
                user_id
            )
            profile = await self.business_profile_repository.get_by_user_id_for_update(
                user_id
            )
            self._require_current_confirmed_profile(
                activation=activation,
                profile=profile,
            )
            if user.country_code != "FR":
                raise ActivationProvisioningBlockedError("unsupported_country")

            subscription = (
                await self.subscription_repository.get_by_user_id_for_update(user_id)
            )
            balance = await self.usage_repository.get_current_balance(user_id=user_id)
            self._require_eligible_access(
                subscription=subscription,
                balance=balance,
                now=self.now(),
            )

            if (
                activation is None
                or activation.provisioning_consented_at is None
                or not activation.provisioning_idempotency_key
            ):
                raise ActivationProvisioningBlockedError(
                    "provisioning_retry_not_allowed"
                )
            provisioning = await self.provisioning_repository.queue_retry(
                user_id=user_id,
                operation_key=activation.provisioning_idempotency_key,
            )
            phone_number = await self.phone_number_repository.get_by_user_id_for_update(
                user_id
            )
            if phone_number is not None:
                raise ActivationProvisioningBlockedError("phone_already_assigned")

            next_attempt = provisioning.attempt_count + 1
            outbox_key = (
                f"activation:phone.provision:{activation.id}:attempt:{next_attempt}"
            )
            await self.outbox_service.add(
                topic="phone.provision",
                aggregate_type="user",
                aggregate_id=user_id,
                idempotency_key=outbox_key,
                payload={"user_id": str(user_id)},
            )
            await self.session.commit()
        except ProvisioningStateConflictError:
            await self.session.rollback()
            raise ActivationProvisioningBlockedError(
                "provisioning_retry_not_allowed"
            ) from None
        except Exception:
            await self.session.rollback()
            raise

        await self._wake_outbox(arq_pool, operation="retry_phone_provisioning")
        return await self.snapshot_service.get(user_id)

    @staticmethod
    def _require_current_confirmed_profile(
        *,
        activation: CustomerActivation | None,
        profile: BusinessProfile | None,
    ) -> None:
        if (
            activation is None
            or activation.profile_confirmed_at is None
            or activation.profile_confirmed_revision is None
        ):
            raise ActivationProvisioningBlockedError("profile_not_confirmed")
        if profile is None:
            raise ActivationProvisioningBlockedError("profile_incomplete")
        if activation.profile_confirmed_revision != profile.content_revision:
            raise ActivationProvisioningBlockedError("profile_confirmation_stale")
        if any(
            ActivationProvisioningService._is_missing(getattr(profile, field))
            for field in REQUIRED_PROFILE_FIELDS
        ):
            raise ActivationProvisioningBlockedError("profile_incomplete")

    @staticmethod
    def _require_eligible_access(
        *,
        subscription: Subscription | None,
        balance: int,
        now: datetime,
    ) -> None:
        if subscription is None:
            raise ActivationProvisioningBlockedError("subscription_missing")
        if subscription.plan_tier != CustomerReadinessPolicy.SUPPORTED_PLAN:
            raise ActivationProvisioningBlockedError("plan_unsupported")
        if (
            subscription.status
            not in CustomerReadinessPolicy.ELIGIBLE_SUBSCRIPTION_STATUSES
        ):
            raise ActivationProvisioningBlockedError(
                "subscription_status_ineligible"
            )
        if (
            subscription.current_period_start is None
            or subscription.current_period_end is None
        ):
            raise ActivationProvisioningBlockedError(
                "subscription_period_missing"
            )
        evaluated_at = CustomerReadinessPolicy._as_utc(now)
        period_start = CustomerReadinessPolicy._as_utc(
            subscription.current_period_start
        )
        period_end = CustomerReadinessPolicy._as_utc(subscription.current_period_end)
        if not period_start <= evaluated_at < period_end:
            raise ActivationProvisioningBlockedError(
                "subscription_period_inactive"
            )
        if balance <= 0:
            raise ActivationProvisioningBlockedError("minutes_exhausted")

    @staticmethod
    def _is_missing(value: object) -> bool:
        return not value or isinstance(value, str) and not value.strip()

    @staticmethod
    async def _wake_outbox(arq_pool, *, operation: str) -> None:
        if arq_pool is None:
            return
        try:
            await arq_pool.enqueue_job("outbox_delivery_job", {})
        except Exception as error:
            logger.warning(
                "outbox wakeup enqueue failed operation=%s error_type=%s",
                operation,
                type(error).__name__,
            )
