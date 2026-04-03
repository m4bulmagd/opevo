from __future__ import annotations

import asyncio
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.subscription_repository import SubscriptionRepository
from app.repositories.usage_repository import UsageRepository
from app.schemas.billing_api import (
    SubscriptionResponse,
    UsageLedgerEntryResponse,
    UsageLedgerListResponse,
    UsageSnapshotResponse,
)


class BillingQueryService:
    def __init__(
        self,
        session: AsyncSession | None = None,
        *,
        subscription_repository: SubscriptionRepository | None = None,
        usage_repository: UsageRepository | None = None,
    ) -> None:
        if subscription_repository is None or usage_repository is None:
            if session is None:
                raise ValueError("session is required when repositories are not provided")
            subscription_repository = subscription_repository or SubscriptionRepository(session)
            usage_repository = usage_repository or UsageRepository(session)

        self.subscription_repository = subscription_repository
        self.usage_repository = usage_repository

    async def get_subscription(self, user_id: UUID | str) -> SubscriptionResponse | None:
        subscription = await self.subscription_repository.get_by_user_id(user_id)
        if subscription is None:
            return None

        return SubscriptionResponse(
            plan_tier=subscription.plan_tier,
            status=subscription.status,
            allocated_minutes=subscription.allocated_minutes,
            current_period_start=subscription.current_period_start,
            current_period_end=subscription.current_period_end,
            stripe_customer_id=subscription.stripe_customer_id,
            stripe_subscription_id=subscription.stripe_subscription_id,
        )

    async def get_usage_snapshot(self, user_id: UUID | str) -> UsageSnapshotResponse:
        subscription, minutes_remaining = await asyncio.gather(
            self.subscription_repository.get_by_user_id(user_id),
            self.usage_repository.get_current_balance(user_id=user_id),
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

    async def get_usage_ledger(self, user_id: UUID | str, *, limit: int) -> UsageLedgerListResponse:
        entries = await self.usage_repository.list_recent_by_user_id(user_id=user_id, limit=limit)
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
