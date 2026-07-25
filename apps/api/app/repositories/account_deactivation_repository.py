from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account_deactivation_operation import (
    AccountDeactivationOperation,
    DeactivationTrigger,
)


@dataclass(frozen=True)
class AccountDeactivationObservabilitySnapshot:
    counts: dict[tuple[str, str], int]
    oldest_incomplete_age_seconds: float
    attention_counts: dict[str, int]


class AccountDeactivationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_incomplete_by_user_id_for_update(
        self,
        user_id: UUID,
    ) -> AccountDeactivationOperation | None:
        result = await self.session.execute(
            select(AccountDeactivationOperation)
            .where(
                AccountDeactivationOperation.user_id == user_id,
                AccountDeactivationOperation.completed_at.is_(None),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def get_latest_by_user_id(
        self,
        user_id: UUID,
    ) -> AccountDeactivationOperation | None:
        return await self.session.scalar(
            select(AccountDeactivationOperation)
            .where(AccountDeactivationOperation.user_id == user_id)
            .order_by(
                AccountDeactivationOperation.requested_at.desc(),
                AccountDeactivationOperation.created_at.desc(),
            )
            .limit(1)
        )

    async def get_by_id(
        self,
        operation_id: UUID,
    ) -> AccountDeactivationOperation | None:
        return await self.session.get(AccountDeactivationOperation, operation_id)

    async def get_by_id_for_update(
        self,
        operation_id: UUID,
    ) -> AccountDeactivationOperation | None:
        result = await self.session.execute(
            select(AccountDeactivationOperation)
            .where(AccountDeactivationOperation.id == operation_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def observability_snapshot(
        self,
        now: datetime,
    ) -> AccountDeactivationObservabilitySnapshot:
        rows = await self.session.execute(
            select(
                AccountDeactivationOperation.trigger,
                AccountDeactivationOperation.status,
                func.count(AccountDeactivationOperation.id),
            ).group_by(
                AccountDeactivationOperation.trigger,
                AccountDeactivationOperation.status,
            )
        )
        counts = {
            (trigger, status): int(count)
            for trigger, status, count in rows
        }
        oldest = await self.session.scalar(
            select(func.min(AccountDeactivationOperation.requested_at)).where(
                AccountDeactivationOperation.completed_at.is_(None)
            )
        )
        if oldest is None:
            oldest_age = 0.0
        else:
            if oldest.tzinfo is None:
                oldest = oldest.replace(tzinfo=now.tzinfo)
            if now.tzinfo is None:
                now = now.replace(tzinfo=oldest.tzinfo)
            oldest_age = max(0.0, (now - oldest).total_seconds())
        attention_rows = await self.session.execute(
            select(
                AccountDeactivationOperation.trigger,
                func.count(AccountDeactivationOperation.id),
            )
            .where(AccountDeactivationOperation.status == "attention_required")
            .group_by(AccountDeactivationOperation.trigger)
        )
        attention_counts = {
            "owner_request": 0,
            "subscription_ended": 0,
        }
        for trigger, count in attention_rows:
            attention_counts[trigger] = int(count)
        return AccountDeactivationObservabilitySnapshot(
            counts=counts,
            oldest_incomplete_age_seconds=oldest_age,
            attention_counts=attention_counts,
        )

    async def create(
        self,
        *,
        user_id: UUID,
        lifecycle_generation: int,
        trigger: DeactivationTrigger,
        requested_at: datetime,
        stripe_subscription_id: str | None = None,
        phone_provider_id: str | None = None,
    ) -> AccountDeactivationOperation:
        operation = AccountDeactivationOperation(
            user_id=user_id,
            lifecycle_generation=lifecycle_generation,
            trigger=trigger,
            requested_at=requested_at,
            stripe_subscription_id=stripe_subscription_id,
            phone_provider_id=phone_provider_id,
        )
        self.session.add(operation)
        await self.session.flush()
        return operation
