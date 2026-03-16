import hashlib
import hmac
import time
from datetime import UTC, datetime

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.providers.telephony.base import TelephonyProvisioningReviewRequired
from app.repositories.notification_repository import NotificationRepository
from app.repositories.subscription_repository import SubscriptionRepository
from app.repositories.usage_repository import UsageRepository
from app.repositories.user_repository import UserRepository
from app.repositories.webhook_event_repository import WebhookEventRepository
from app.services.telephony_service import TelephonyService


PLAN_MINUTES = {
    "starter": 60,
    "standard": 120,
}


class BillingService:
    def __init__(self, session: AsyncSession, telephony_service: TelephonyService | None = None) -> None:
        self.session = session
        self.settings = get_settings()
        self.user_repository = UserRepository(session)
        self.subscription_repository = SubscriptionRepository(session)
        self.usage_repository = UsageRepository(session)
        self.notification_repository = NotificationRepository(session)
        self.webhook_event_repository = WebhookEventRepository(session)
        self.telephony_service = telephony_service or TelephonyService(session)

    def verify_signature(self, payload: bytes, signature_header: str | None) -> None:
        if not self.settings.stripe_webhook_secret:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Stripe secret not configured")
        if not signature_header:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing Stripe signature")

        timestamp: str | None = None
        provided_signatures: list[str] = []
        for part in signature_header.split(","):
            key, sep, value = part.partition("=")
            if not sep:
                continue
            if key == "t":
                timestamp = value
            elif key == "v1":
                provided_signatures.append(value)

        if not timestamp or not provided_signatures:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Stripe signature")

        try:
            webhook_timestamp = int(timestamp)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Stripe signature") from exc

        if abs(int(time.time()) - webhook_timestamp) > 300:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Expired Stripe signature")

        signed_payload = timestamp.encode("utf-8") + b"." + payload
        expected = hmac.new(
            self.settings.stripe_webhook_secret.encode("utf-8"),
            signed_payload,
            hashlib.sha256,
        ).hexdigest()
        if not any(hmac.compare_digest(provided, expected) for provided in provided_signatures):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Stripe signature")

    async def handle_event(self, envelope: dict) -> None:
        is_new = await self.webhook_event_repository.record_if_new(
            provider="stripe",
            external_event_id=envelope["id"],
            event_type=envelope["type"],
            payload=envelope,
        )
        if not is_new:
            await self.session.commit()
            return

        event_type = envelope["type"]
        event_object = envelope["data"]["object"]

        if event_type == "customer.subscription.created":
            await self._handle_subscription_created(event_object)
        elif event_type == "invoice.paid":
            await self._handle_invoice_paid(event_object)

        await self.session.commit()

    async def _handle_subscription_created(self, event_object: dict) -> None:
        clerk_user_id = event_object.get("metadata", {}).get("clerk_user_id")
        if not clerk_user_id:
            return

        user = await self.user_repository.get_by_clerk_user_id(clerk_user_id)
        if user is None:
            return

        plan_tier = event_object["items"]["data"][0]["price"].get("lookup_key", "starter")
        allocated_minutes = PLAN_MINUTES.get(plan_tier, 60)
        current_period_start, current_period_end = self._extract_subscription_period_bounds(event_object)

        await self.subscription_repository.upsert_by_stripe_subscription_id(
            user_id=user.id,
            stripe_customer_id=event_object["customer"],
            stripe_subscription_id=event_object["id"],
            plan_tier=plan_tier,
            status=event_object.get("status", "active"),
            allocated_minutes=allocated_minutes,
            current_period_start=current_period_start,
            current_period_end=current_period_end,
        )
        try:
            await self.telephony_service.provision_number(user.id, country_code=user.country_code or "FR")
        except TelephonyProvisioningReviewRequired as exc:
            await self.notification_repository.create(
                user_id=user.id,
                call_id=None,
                notification_type="phone_number_provisioning_review_required",
                status="pending",
                payload=exc.payload,
            )
        await self.usage_repository.create(
            user_id=user.id,
            event_type="subscription_activated",
            minutes_delta=allocated_minutes,
            balance_after=allocated_minutes,
        )

    async def _handle_invoice_paid(self, event_object: dict) -> None:
        subscription_id = self._extract_invoice_subscription_id(event_object)
        if not subscription_id:
            return

        subscription = await self.subscription_repository.get_by_stripe_subscription_id(subscription_id)
        if subscription is None:
            return

        plan_tier = self._extract_invoice_plan_tier(event_object) or subscription.plan_tier
        allocated_minutes = PLAN_MINUTES.get(plan_tier, subscription.allocated_minutes)
        subscription.plan_tier = plan_tier
        subscription.allocated_minutes = allocated_minutes
        subscription.status = "active"

        await self.usage_repository.create(
            user_id=subscription.user_id,
            event_type="invoice_paid_reset",
            minutes_delta=allocated_minutes,
            balance_after=allocated_minutes,
        )

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
