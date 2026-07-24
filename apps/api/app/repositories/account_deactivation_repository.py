from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account_deactivation_operation import (
    AccountDeactivationOperation,
    DeactivationTrigger,
)


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
