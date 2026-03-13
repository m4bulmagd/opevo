from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, clerk_user_id: str, email: str) -> User:
        user = User(clerk_user_id=clerk_user_id, email=email)
        self.session.add(user)
        await self.session.flush()
        return user

    async def get_by_clerk_user_id(self, clerk_user_id: str) -> User | None:
        result = await self.session.execute(
            select(User).where(User.clerk_user_id == clerk_user_id)
        )
        return result.scalar_one_or_none()
