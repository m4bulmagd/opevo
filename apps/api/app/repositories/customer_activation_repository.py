from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.customer_activation import CustomerActivation
from app.models.user import User


class CustomerActivationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_user_id(self, user_id: UUID) -> CustomerActivation | None:
        result = await self.session.execute(
            select(CustomerActivation).where(CustomerActivation.user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def get_or_create_for_update(self, user_id: UUID) -> CustomerActivation:
        await self.session.scalar(
            select(User.id).where(User.id == user_id).with_for_update()
        )
        result = await self.session.execute(
            select(CustomerActivation)
            .where(CustomerActivation.user_id == user_id)
            .with_for_update()
        )
        activation = result.scalar_one_or_none()
        if activation is None:
            activation = CustomerActivation(user_id=user_id)
            self.session.add(activation)
            await self.session.flush()
        return activation
