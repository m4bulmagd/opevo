from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.business_profile import BusinessProfile
from app.models.user import User


class BusinessProfileRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_user_id(self, user_id: UUID) -> BusinessProfile | None:
        result = await self.session.execute(
            select(BusinessProfile).where(BusinessProfile.user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def get_by_user_id_for_update(
        self,
        user_id: UUID,
    ) -> BusinessProfile | None:
        result = await self.session.execute(
            select(BusinessProfile)
            .where(BusinessProfile.user_id == user_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def get_or_create_for_update(self, user_id: UUID) -> BusinessProfile:
        await self.session.scalar(
            select(User.id).where(User.id == user_id).with_for_update()
        )
        result = await self.session.execute(
            select(BusinessProfile)
            .where(BusinessProfile.user_id == user_id)
            .with_for_update()
        )
        profile = result.scalar_one_or_none()
        if profile is None:
            profile = BusinessProfile(user_id=user_id)
            self.session.add(profile)
            await self.session.flush()
        return profile
