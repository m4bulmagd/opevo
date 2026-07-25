from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(self, user_id: UUID) -> User | None:
        return await self.session.get(User, user_id)

    async def get_by_id_for_update(self, user_id: UUID) -> User | None:
        result = await self.session.execute(
            select(User)
            .where(User.id == user_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def start_deactivation(self, user: User) -> User:
        user.status = "deactivating"
        user.lifecycle_generation += 1
        await self.session.flush()
        return user

    async def reactivate(
        self,
        user: User,
        *,
        lifecycle_generation: int,
    ) -> bool:
        if (
            user.status != "inactive"
            or user.lifecycle_generation != lifecycle_generation
        ):
            return False
        user.status = "active"
        await self.session.flush()
        return True

    async def create(self, clerk_user_id: str, email: str) -> User:
        user = User(clerk_user_id=clerk_user_id, email=email)
        self.session.add(user)
        await self.session.flush()
        return user

    async def acquire_bootstrap_lock(self, *, external_user_id: str) -> None:
        if self.session.get_bind().dialect.name != "postgresql":
            return
        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
            {"lock_key": f"user.bootstrap:{external_user_id}"},
        )

    async def get_by_clerk_user_id(self, clerk_user_id: str) -> User | None:
        result = await self.session.execute(
            select(User).where(User.clerk_user_id == clerk_user_id)
        )
        return result.scalar_one_or_none()
