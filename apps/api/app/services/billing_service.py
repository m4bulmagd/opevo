import hashlib
import hmac
from datetime import UTC, datetime

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
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
        self.webhook_event_repository = WebhookEventRepository(session)
        self.telephony_service = telephony_service or TelephonyService(session)

    def verify_signature(self, payload: bytes, signature_header: str | None) -> None:
        if not self.settings.stripe_webhook_secret:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Stripe secret not configured")
        if not signature_header:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing Stripe signature")

        parts = dict(part.split("=", 1) for part in signature_header.split(",") if "=" in part)
        provided = parts.get("v1")
        expected = hmac.new(
            self.settings.stripe_webhook_secret.encode("utf-8"),
            payload,
            hashlib.sha256,
        ).hexdigest()
        if not provided or not hmac.compare_digest(provided, expected):
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

        await self.subscription_repository.upsert_by_stripe_subscription_id(
            user_id=user.id,
            stripe_customer_id=event_object["customer"],
            stripe_subscription_id=event_object["id"],
            plan_tier=plan_tier,
            status=event_object.get("status", "active"),
            allocated_minutes=allocated_minutes,
            current_period_start=datetime.fromtimestamp(event_object["current_period_start"], UTC),
            current_period_end=datetime.fromtimestamp(event_object["current_period_end"], UTC),
        )
        await self.telephony_service.provision_number(user.id, country_code=user.country_code or "FR")
        await self.usage_repository.create(
            user_id=user.id,
            event_type="subscription_activated",
            minutes_delta=allocated_minutes,
            balance_after=allocated_minutes,
        )

    async def _handle_invoice_paid(self, event_object: dict) -> None:
        subscription = await self.subscription_repository.get_by_stripe_subscription_id(event_object["subscription"])
        if subscription is None:
            return

        plan_tier = event_object["lines"]["data"][0]["price"].get("lookup_key", subscription.plan_tier)
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
