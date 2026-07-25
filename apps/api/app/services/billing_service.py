from datetime import UTC, datetime
import logging
from uuid import UUID

from arq.connections import ArqRedis
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.repositories.account_deactivation_repository import (
    AccountDeactivationRepository,
)
from app.repositories.phone_number_repository import PhoneNumberRepository
from app.repositories.subscription_repository import (
    StripeSubscriptionConflictError,
    StripeSubscriptionDataError,
    SubscriptionRepository,
)
from app.repositories.user_repository import UserRepository
from app.repositories.webhook_event_repository import WebhookEventRepository
from app.services.account_lifecycle_service import AccountLifecycleService
from app.services.outbox_service import OutboxService
from app.services.subscription_access_policy import SubscriptionAccessPolicy
from app.services.usage_accounting_service import UsageAccountingService


PLAN_MINUTES = {
    "starter": 60,
}

RECOGNIZED_SUBSCRIPTION_STATUSES = frozenset(
    {
        "trialing",
        "active",
        "past_due",
        "unpaid",
        "canceled",
        "incomplete",
        "incomplete_expired",
        "paused",
    }
)

STRIPE_EVENT_HANDLER_MAP = {
    "customer.subscription.created": "_handle_subscription_event",
    "customer.subscription.updated": "_handle_subscription_event",
    "customer.subscription.deleted": "_handle_subscription_event",
    "invoice.paid": "_handle_invoice_paid",
    "invoice.payment_failed": "_handle_invoice_payment_failed",
}

logger = logging.getLogger(__name__)


class UnsupportedStripeLifecycleError(ValueError):
    pass


class StripeLifecycleConflictError(RuntimeError):
    pass


class BillingService:
    def __init__(
        self,
        session: AsyncSession,
        arq_pool: ArqRedis | None = None,
    ) -> None:
        self.session = session
        self.settings = get_settings()
        self.user_repository = UserRepository(session)
        self.account_deactivation_repository = AccountDeactivationRepository(session)
        self.subscription_repository = SubscriptionRepository(session)
        self.phone_number_repository = PhoneNumberRepository(session)
        self.usage_accounting_service = UsageAccountingService(session)
        self.webhook_event_repository = WebhookEventRepository(session)
        self.outbox_service = OutboxService(session)
        self.account_lifecycle_service = AccountLifecycleService(
            session,
            account_deactivation_repository=self.account_deactivation_repository,
            phone_number_repository=self.phone_number_repository,
            subscription_repository=self.subscription_repository,
            outbox_service=self.outbox_service,
            user_repository=self.user_repository,
        )
        self.arq_pool = arq_pool
        self._outbox_wakeup_needed = False

    def verify_signature(self, payload: bytes, signature_header: str | None) -> None:
        if not self.settings.stripe_webhook_secret:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Stripe secret not configured",
            )

        from app.core.webhook_verifier import verify_stripe_signature

        verify_stripe_signature(
            secret=self.settings.stripe_webhook_secret,
            payload=payload,
            signature_header=signature_header,
        )

    async def handle_event(self, envelope: dict) -> bool:
        is_new = await self.webhook_event_repository.record_if_new(
            provider="stripe",
            external_event_id=envelope["id"],
            event_type=envelope["type"],
            payload=envelope,
        )
        if not is_new:
            await self.session.commit()
            return False

        event_type = envelope["type"]
        event_object = envelope["data"]["object"]

        handler_name = STRIPE_EVENT_HANDLER_MAP.get(event_type)
        if handler_name is not None:
            handler = getattr(self, handler_name)
            event_created_at = self._stripe_timestamp(envelope["created"])
            try:
                await handler(
                    event_object,
                    envelope["id"],
                    event_type,
                    event_created_at,
                )
            except StripeSubscriptionConflictError as exc:
                raise StripeLifecycleConflictError from exc

        await self.session.commit()
        await self._enqueue_outbox_wakeup()
        return True

    async def _handle_subscription_event(
        self,
        event_object: dict,
        event_id: str,
        event_type: str,
        event_created_at: datetime,
    ) -> None:
        stripe_subscription_id = event_object["id"]
        existing_user_id = (
            await self.subscription_repository.get_user_id_by_stripe_subscription_id(
                stripe_subscription_id
            )
        )
        clerk_user_id = event_object.get("metadata", {}).get("clerk_user_id")
        discovered_user = (
            await self.user_repository.get_by_clerk_user_id(clerk_user_id)
            if clerk_user_id
            else None
        )
        user_id = (
            discovered_user.id if discovered_user is not None else existing_user_id
        )
        if user_id is None:
            return
        user = await self.user_repository.get_by_id_for_update(user_id)
        if user is None:
            return
        incomplete_operation = await self.account_deactivation_repository.get_incomplete_by_user_id_for_update(
            user_id
        )
        current_subscription = (
            await self.subscription_repository.get_by_user_id_for_update(user_id)
        )

        plan_tier = self._extract_subscription_plan_tier(event_object)
        allocated_minutes = (
            self._require_plan_minutes(plan_tier) if plan_tier is not None else None
        )

        subscription_status = event_object.get("status")
        if (
            subscription_status is None
            and event_type == "customer.subscription.deleted"
        ):
            subscription_status = "canceled"
        subscription_status = self._require_subscription_status(subscription_status)
        is_final_cancellation = bool(
            subscription_status == "canceled"
            and event_type
            in {
                "customer.subscription.updated",
                "customer.subscription.deleted",
            }
        )
        subscription_created_at = self._stripe_timestamp(event_object.get("created"))

        current_period_start, current_period_end = (
            self._extract_subscription_period_bounds(event_object)
        )
        cancel_at_period_end, cancellation_effective_at = (
            self._extract_scheduled_cancellation(
                event_object,
                current_period_end=current_period_end,
            )
        )

        exact_incomplete_terminal = bool(
            is_final_cancellation
            and incomplete_operation is not None
            and incomplete_operation.stripe_subscription_id == stripe_subscription_id
            and current_subscription is not None
            and current_subscription.stripe_subscription_id == stripe_subscription_id
        )
        lifecycle_generation = self._metadata_lifecycle_generation(
            event_object.get("metadata", {})
        )
        if exact_incomplete_terminal:
            assert current_subscription is not None
            lifecycle_generation = current_subscription.lifecycle_generation
        elif lifecycle_generation is None:
            if user.lifecycle_generation != 1:
                return
            lifecycle_generation = 1
        if (
            not exact_incomplete_terminal
            and lifecycle_generation != user.lifecycle_generation
        ):
            return
        if user.status == "deactivating" and not exact_incomplete_terminal:
            return
        if (
            is_final_cancellation
            and current_subscription is not None
            and current_subscription.stripe_subscription_id != stripe_subscription_id
        ):
            return
        is_replacement = bool(
            current_subscription is None
            or current_subscription.stripe_subscription_id != stripe_subscription_id
        )
        is_authorized_progression = bool(
            current_subscription is not None
            and current_subscription.stripe_subscription_id == stripe_subscription_id
            and current_subscription.lifecycle_generation == lifecycle_generation
            and not SubscriptionAccessPolicy.can_replace_subscription(
                current_subscription.status
            )
        )
        account_was_inactive = user.status == "inactive"
        if account_was_inactive:
            if not is_replacement and not is_authorized_progression:
                return
            phone_number = await self.phone_number_repository.get_by_user_id_for_update(
                user_id
            )
            safe_reactivation_boundary = bool(
                incomplete_operation is None and phone_number is None
            )
            if is_replacement:
                safe_reactivation_boundary = (
                    safe_reactivation_boundary
                    and SubscriptionAccessPolicy.can_start_checkout(
                        account_status=user.status,
                        subscription_status=(
                            current_subscription.status
                            if current_subscription is not None
                            else None
                        ),
                        has_incomplete_deactivation=False,
                        has_phone=False,
                    )
                )
            if not safe_reactivation_boundary:
                return

        stripe_customer_id = event_object.get("customer")
        try:
            subscription = (
                await self.subscription_repository.upsert_by_stripe_subscription_id(
                    user_id=user_id,
                    stripe_customer_id=stripe_customer_id,
                    stripe_subscription_id=stripe_subscription_id,
                    plan_tier=plan_tier,
                    status=subscription_status,
                    allocated_minutes=allocated_minutes,
                    current_period_start=current_period_start,
                    current_period_end=current_period_end,
                    stripe_subscription_created_at=subscription_created_at,
                    last_stripe_event_created_at=event_created_at,
                    lifecycle_generation=lifecycle_generation,
                    cancel_at_period_end=cancel_at_period_end,
                    cancellation_effective_at=cancellation_effective_at,
                )
            )
        except StripeSubscriptionDataError as exc:
            raise UnsupportedStripeLifecycleError from exc

        if subscription is None:
            return

        if (
            user.status == "inactive"
            and subscription.lifecycle_generation == user.lifecycle_generation
            and subscription.status in {"active", "trialing"}
        ):
            await self.user_repository.reactivate(
                user,
                lifecycle_generation=subscription.lifecycle_generation,
            )

        if account_was_inactive:
            return

        if is_final_cancellation:
            operation = await self.account_lifecycle_service.request_in_transaction(
                user_id=subscription.user_id,
                trigger="subscription_ended",
                stripe_subscription_id=subscription.stripe_subscription_id,
            )
            if operation is not None:
                self._outbox_wakeup_needed = True
            return

        await self._add_disable_intent_if_needed(
            subscription=subscription,
            event_id=event_id,
            event_type=event_type,
        )

    async def _handle_invoice_paid(
        self,
        event_object: dict,
        _event_id: str,
        _event_type: str,
        event_created_at: datetime,
    ) -> None:
        if not SubscriptionAccessPolicy.should_grant_invoice(
            event_object.get("status", ""),
        ):
            return

        subscription_id = self._extract_invoice_subscription_id(event_object)
        if not subscription_id:
            return

        invoice_id = event_object.get("id")
        if not isinstance(invoice_id, str) or not invoice_id:
            return
        await self.usage_accounting_service.acquire_invoice_grant_lock(
            invoice_id=invoice_id
        )
        context = await self._resolve_current_invoice_context(
            event_object,
            subscription_id=subscription_id,
        )
        if context is None:
            return
        user_id, lifecycle_generation = context
        (
            subscription,
            should_apply,
        ) = await self.subscription_repository.resolve_invoice_target_for_update(
            stripe_subscription_id=subscription_id,
            user_id=user_id,
            incoming_status="active",
            event_created_at=event_created_at,
        )
        if not should_apply:
            return
        if subscription is None:
            subscription = await self._bootstrap_subscription_from_invoice(
                subscription_id,
                event_object,
                event_created_at=event_created_at,
                lifecycle_generation=lifecycle_generation,
            )
            if subscription is None:
                return

        plan_tier = (
            self._extract_invoice_plan_tier(event_object) or subscription.plan_tier
        )
        allocated_minutes = self._require_plan_minutes(plan_tier)
        subscription.plan_tier = plan_tier
        subscription.allocated_minutes = allocated_minutes
        subscription.status = "active"
        self.subscription_repository.advance_known_event_watermark(
            subscription,
            event_created_at,
        )

        grant = await self.usage_accounting_service.grant_invoice(
            user_id=subscription.user_id,
            invoice_id=invoice_id,
            minutes=allocated_minutes,
        )

        if grant.already_granted:
            return

    async def _handle_invoice_payment_failed(
        self,
        event_object: dict,
        event_id: str,
        event_type: str,
        event_created_at: datetime,
    ) -> None:
        subscription_id = self._extract_invoice_subscription_id(event_object)
        if not subscription_id:
            return

        context = await self._resolve_current_invoice_context(
            event_object,
            subscription_id=subscription_id,
        )
        if context is None:
            return
        user_id, lifecycle_generation = context
        (
            subscription,
            should_apply,
        ) = await self.subscription_repository.resolve_invoice_target_for_update(
            stripe_subscription_id=subscription_id,
            user_id=user_id,
            incoming_status="past_due",
            event_created_at=event_created_at,
        )
        if not should_apply:
            return
        if subscription is None:
            subscription = await self._bootstrap_subscription_from_invoice(
                subscription_id,
                event_object,
                status="past_due",
                event_created_at=event_created_at,
                lifecycle_generation=lifecycle_generation,
            )
            if subscription is None:
                return
        else:
            subscription.status = "past_due"
            self.subscription_repository.advance_known_event_watermark(
                subscription,
                event_created_at,
            )

        await self._add_disable_intent_if_needed(
            subscription=subscription,
            event_id=event_id,
            event_type=event_type,
        )

    async def _bootstrap_subscription_from_invoice(
        self,
        subscription_id: str,
        event_object: dict,
        *,
        status: str = "active",
        event_created_at: datetime,
        lifecycle_generation: int,
    ):
        clerk_user_id = self._extract_invoice_clerk_user_id(event_object)
        if not clerk_user_id:
            return None

        user = await self.user_repository.get_by_clerk_user_id(clerk_user_id)
        if user is None:
            return None

        plan_tier = self._extract_invoice_plan_tier(
            event_object
        ) or self._extract_invoice_plan_tier_from_metadata(event_object)
        allocated_minutes = self._require_plan_minutes(plan_tier)

        return await self.subscription_repository.upsert_by_stripe_subscription_id(
            user_id=user.id,
            stripe_customer_id=event_object["customer"],
            stripe_subscription_id=subscription_id,
            plan_tier=plan_tier,
            status=self._require_subscription_status(status),
            allocated_minutes=allocated_minutes,
            current_period_start=None,
            current_period_end=None,
            stripe_subscription_created_at=None,
            last_stripe_event_created_at=event_created_at,
            lifecycle_generation=lifecycle_generation,
        )

    async def _invoice_user_id(self, event_object: dict):
        clerk_user_id = self._extract_invoice_clerk_user_id(event_object)
        if not clerk_user_id:
            return None
        user = await self.user_repository.get_by_clerk_user_id(clerk_user_id)
        return user.id if user is not None else None

    async def _resolve_current_invoice_context(
        self,
        event_object: dict,
        *,
        subscription_id: str,
    ) -> tuple[UUID, int] | None:
        user_id = await self._invoice_user_id(event_object)
        if user_id is None:
            user_id = await self.subscription_repository.get_user_id_by_stripe_subscription_id(
                subscription_id
            )
        if user_id is None:
            return None
        user = await self.user_repository.get_by_id_for_update(user_id)
        if user is None or user.status != "active":
            return None
        lifecycle_generation = self._invoice_lifecycle_generation(event_object)
        if lifecycle_generation is None:
            if user.lifecycle_generation != 1:
                return None
            lifecycle_generation = 1
        if lifecycle_generation != user.lifecycle_generation:
            return None
        current_subscription = (
            await self.subscription_repository.get_by_user_id_for_update(user_id)
        )
        if (
            current_subscription is not None
            and current_subscription.lifecycle_generation != lifecycle_generation
        ):
            return None
        return user_id, lifecycle_generation

    async def _add_disable_intent_if_needed(
        self,
        *,
        subscription,
        event_id: str,
        event_type: str,
    ) -> None:
        if SubscriptionAccessPolicy.can_route(
            subscription.status,
            subscription.current_period_end,
        ):
            return

        await self._add_phone_intent(
            topic="phone.disable",
            user_id=subscription.user_id,
            idempotency_key=f"stripe:{event_type}:{event_id}",
        )

    async def _add_phone_intent(
        self,
        *,
        topic: str,
        user_id,
        idempotency_key: str,
        lifecycle_generation: int | None = None,
    ) -> None:
        payload: dict[str, object] = {"user_id": str(user_id)}
        if lifecycle_generation is not None:
            payload["lifecycle_generation"] = lifecycle_generation
        await self.outbox_service.add(
            topic=topic,
            aggregate_type="user",
            aggregate_id=user_id,
            idempotency_key=idempotency_key,
            payload=payload,
        )
        self._outbox_wakeup_needed = True

    async def _enqueue_outbox_wakeup(self) -> None:
        if not self._outbox_wakeup_needed or self.arq_pool is None:
            return
        try:
            await self.arq_pool.enqueue_job("outbox_delivery_job", {})
        except Exception as error:
            logger.warning(
                "outbox wakeup enqueue failed operation=stripe_webhook error_type=%s",
                type(error).__name__,
            )

    @staticmethod
    def _extract_subscription_plan_tier(event_object: dict) -> str | None:
        for item in event_object.get("items", {}).get("data", []):
            lookup_key = item.get("price", {}).get("lookup_key")
            if lookup_key:
                return lookup_key
        return event_object.get("metadata", {}).get("plan_tier")

    @staticmethod
    def _require_plan_minutes(plan_tier: str | None) -> int:
        if plan_tier not in PLAN_MINUTES:
            raise UnsupportedStripeLifecycleError(
                "Stripe subscription plan is unsupported"
            )
        return PLAN_MINUTES[plan_tier]

    @staticmethod
    def _require_subscription_status(subscription_status: str | None) -> str:
        if subscription_status not in RECOGNIZED_SUBSCRIPTION_STATUSES:
            raise UnsupportedStripeLifecycleError(
                "Stripe subscription status is unsupported"
            )
        return subscription_status

    @staticmethod
    def _extract_invoice_subscription_id(event_object: dict) -> str | None:
        subscription_id = event_object.get("subscription")
        if subscription_id:
            return subscription_id

        subscription_details = event_object.get("parent", {}).get(
            "subscription_details", {}
        )
        subscription_id = subscription_details.get("subscription")
        if subscription_id:
            return subscription_id

        for line in event_object.get("lines", {}).get("data", []):
            subscription_item_details = line.get("parent", {}).get(
                "subscription_item_details", {}
            )
            subscription_id = subscription_item_details.get("subscription")
            if subscription_id:
                return subscription_id

        return None

    @staticmethod
    def _extract_invoice_plan_tier(event_object: dict) -> str | None:
        for line in event_object.get("lines", {}).get("data", []):
            lookup_key = line.get("price", {}).get("lookup_key")
            if lookup_key:
                return lookup_key
        return None

    @staticmethod
    def _extract_invoice_clerk_user_id(event_object: dict) -> str | None:
        subscription_details = event_object.get("parent", {}).get(
            "subscription_details", {}
        )
        metadata = subscription_details.get("metadata", {})
        clerk_user_id = metadata.get("clerk_user_id")
        if clerk_user_id:
            return clerk_user_id
        return event_object.get("metadata", {}).get("clerk_user_id")

    @staticmethod
    def _extract_invoice_plan_tier_from_metadata(event_object: dict) -> str | None:
        subscription_details = event_object.get("parent", {}).get(
            "subscription_details", {}
        )
        metadata = subscription_details.get("metadata", {})
        plan_tier = metadata.get("plan_tier")
        if plan_tier:
            return plan_tier
        return event_object.get("metadata", {}).get("plan_tier")

    @classmethod
    def _invoice_lifecycle_generation(cls, event_object: dict) -> int | None:
        subscription_details = event_object.get("parent", {}).get(
            "subscription_details",
            {},
        )
        generation = cls._metadata_lifecycle_generation(
            subscription_details.get("metadata", {})
        )
        if generation is not None:
            return generation
        return cls._metadata_lifecycle_generation(event_object.get("metadata", {}))

    @staticmethod
    def _metadata_lifecycle_generation(metadata: object) -> int | None:
        if not isinstance(metadata, dict):
            return None
        raw_generation = metadata.get("lifecycle_generation")
        if raw_generation is None:
            return None
        if not isinstance(raw_generation, str) or not raw_generation.isdigit():
            return 0
        generation = int(raw_generation)
        return generation if generation > 0 else 0

    @classmethod
    def _extract_scheduled_cancellation(
        cls,
        event_object: dict,
        *,
        current_period_end: datetime | None,
    ) -> tuple[bool, datetime | None]:
        cancel_at_period_end = event_object.get("cancel_at_period_end") is True
        if not cancel_at_period_end:
            return False, None
        cancel_at = event_object.get("cancel_at")
        if cancel_at is not None:
            return True, cls._stripe_timestamp(cancel_at)
        return True, current_period_end

    @staticmethod
    def _extract_subscription_period_bounds(
        event_object: dict,
    ) -> tuple[datetime | None, datetime | None]:
        start = event_object.get("current_period_start")
        end = event_object.get("current_period_end")
        if start is not None and end is not None:
            return (
                datetime.fromtimestamp(start, UTC),
                datetime.fromtimestamp(end, UTC),
            )

        for item in event_object.get("items", {}).get("data", []):
            item_start = item.get("current_period_start")
            item_end = item.get("current_period_end")
            if item_start is not None and item_end is not None:
                return (
                    datetime.fromtimestamp(item_start, UTC),
                    datetime.fromtimestamp(item_end, UTC),
                )

        billing_cycle_anchor = event_object.get("billing_cycle_anchor")
        if billing_cycle_anchor is not None:
            anchor = datetime.fromtimestamp(billing_cycle_anchor, UTC)
            return (anchor, None)

        return (None, None)

    @staticmethod
    def _stripe_timestamp(value: object) -> datetime:
        if not isinstance(value, int) or isinstance(value, bool):
            raise UnsupportedStripeLifecycleError(
                "Stripe event creation timestamp is invalid"
            )
        try:
            return datetime.fromtimestamp(value, UTC)
        except (OverflowError, OSError, ValueError):
            raise UnsupportedStripeLifecycleError(
                "Stripe event creation timestamp is invalid"
            ) from None
