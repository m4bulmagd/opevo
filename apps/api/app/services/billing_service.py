from datetime import UTC, datetime
import logging

from arq.connections import ArqRedis
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.repositories.phone_number_repository import PhoneNumberRepository
from app.repositories.subscription_repository import (
    StripeSubscriptionConflictError,
    StripeSubscriptionDataError,
    SubscriptionRepository,
)
from app.repositories.user_repository import UserRepository
from app.repositories.webhook_event_repository import WebhookEventRepository
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
        self.subscription_repository = SubscriptionRepository(session)
        self.phone_number_repository = PhoneNumberRepository(session)
        self.usage_accounting_service = UsageAccountingService(session)
        self.webhook_event_repository = WebhookEventRepository(session)
        self.outbox_service = OutboxService(session)
        self.arq_pool = arq_pool
        self._outbox_wakeup_needed = False

    def verify_signature(self, payload: bytes, signature_header: str | None) -> None:
        if not self.settings.stripe_webhook_secret:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Stripe secret not configured")
            
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
        user = (
            await self.user_repository.get_by_clerk_user_id(clerk_user_id)
            if clerk_user_id
            else None
        )
        user_id = user.id if user is not None else existing_user_id
        if user_id is None:
            return

        plan_tier = self._extract_subscription_plan_tier(event_object)
        allocated_minutes = (
            self._require_plan_minutes(plan_tier)
            if plan_tier is not None
            else None
        )

        subscription_status = event_object.get("status")
        if subscription_status is None and event_type == "customer.subscription.deleted":
            subscription_status = "canceled"
        subscription_status = self._require_subscription_status(subscription_status)
        subscription_created_at = self._stripe_timestamp(event_object.get("created"))

        current_period_start, current_period_end = self._extract_subscription_period_bounds(event_object)

        stripe_customer_id = event_object.get("customer")
        try:
            subscription = await self.subscription_repository.upsert_by_stripe_subscription_id(
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
            )
        except StripeSubscriptionDataError as exc:
            raise UnsupportedStripeLifecycleError from exc

        if subscription is None:
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
        user_id = await self._invoice_user_id(event_object)
        if user_id is None:
            user_id = (
                await self.subscription_repository.get_user_id_by_stripe_subscription_id(
                    subscription_id
                )
            )
        if user_id is None:
            return
        locked_user = await self.user_repository.get_by_id_for_update(user_id)
        if locked_user is None:
            return
        subscription, should_apply = (
            await self.subscription_repository.resolve_invoice_target_for_update(
                stripe_subscription_id=subscription_id,
                user_id=user_id,
                incoming_status="active",
                event_created_at=event_created_at,
            )
        )
        if not should_apply:
            return
        if subscription is None:
            subscription = await self._bootstrap_subscription_from_invoice(
                subscription_id,
                event_object,
                event_created_at=event_created_at,
            )
            if subscription is None:
                return

        plan_tier = self._extract_invoice_plan_tier(event_object) or subscription.plan_tier
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
        phone_number = await self.phone_number_repository.get_by_user_id(
            subscription.user_id
        )
        if phone_number is not None:
            await self._add_phone_intent(
                topic="phone.enable",
                user_id=subscription.user_id,
                idempotency_key=f"stripe:invoice:{invoice_id}:phone.enable",
            )

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

        user_id = await self._invoice_user_id(event_object)
        subscription, should_apply = (
            await self.subscription_repository.resolve_invoice_target_for_update(
                stripe_subscription_id=subscription_id,
                user_id=user_id,
                incoming_status="past_due",
                event_created_at=event_created_at,
            )
        )
        if not should_apply:
            return
        if subscription is None:
            subscription = await self._bootstrap_subscription_from_invoice(
                subscription_id,
                event_object,
                status="past_due",
                event_created_at=event_created_at,
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
    ):
        clerk_user_id = self._extract_invoice_clerk_user_id(event_object)
        if not clerk_user_id:
            return None

        user = await self.user_repository.get_by_clerk_user_id(clerk_user_id)
        if user is None:
            return None

        plan_tier = (
            self._extract_invoice_plan_tier(event_object)
            or self._extract_invoice_plan_tier_from_metadata(event_object)
        )
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
        )

    async def _invoice_user_id(self, event_object: dict):
        clerk_user_id = self._extract_invoice_clerk_user_id(event_object)
        if not clerk_user_id:
            return None
        user = await self.user_repository.get_by_clerk_user_id(clerk_user_id)
        return user.id if user is not None else None

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
    ) -> None:
        await self.outbox_service.add(
            topic=topic,
            aggregate_type="user",
            aggregate_id=user_id,
            idempotency_key=idempotency_key,
            payload={"user_id": str(user_id)},
        )
        self._outbox_wakeup_needed = True

    async def _enqueue_outbox_wakeup(self) -> None:
        if not self._outbox_wakeup_needed or self.arq_pool is None:
            return
        try:
            await self.arq_pool.enqueue_job("outbox_delivery_job", {})
        except Exception as error:
            logger.warning(
                "outbox wakeup enqueue failed operation=stripe_webhook "
                "error_type=%s",
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

        subscription_details = event_object.get("parent", {}).get("subscription_details", {})
        subscription_id = subscription_details.get("subscription")
        if subscription_id:
            return subscription_id

        for line in event_object.get("lines", {}).get("data", []):
            subscription_item_details = line.get("parent", {}).get("subscription_item_details", {})
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
        subscription_details = event_object.get("parent", {}).get("subscription_details", {})
        metadata = subscription_details.get("metadata", {})
        clerk_user_id = metadata.get("clerk_user_id")
        if clerk_user_id:
            return clerk_user_id
        return event_object.get("metadata", {}).get("clerk_user_id")

    @staticmethod
    def _extract_invoice_plan_tier_from_metadata(event_object: dict) -> str | None:
        subscription_details = event_object.get("parent", {}).get("subscription_details", {})
        metadata = subscription_details.get("metadata", {})
        plan_tier = metadata.get("plan_tier")
        if plan_tier:
            return plan_tier
        return event_object.get("metadata", {}).get("plan_tier")

    @staticmethod
    def _extract_subscription_period_bounds(event_object: dict) -> tuple[datetime | None, datetime | None]:
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
