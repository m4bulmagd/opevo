from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_config import AgentConfig


class AgentConfigRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_default(self, user_id: UUID) -> AgentConfig:
        config = AgentConfig(user_id=user_id)
        self.session.add(config)
        await self.session.flush()
        return config

    async def get_by_user_id(self, user_id: UUID) -> AgentConfig | None:
        result = await self.session.execute(select(AgentConfig).where(AgentConfig.user_id == user_id))
        return result.scalar_one_or_none()

    async def update_fields(self, config: AgentConfig, updates: dict[str, object]) -> AgentConfig:
        for field, value in updates.items():
            setattr(config, field, value)
        await self.session.flush()
        return config
