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

    async def get_or_create_default(self, user_id: UUID) -> AgentConfig:
        config = await self.get_by_user_id(user_id)
        if config is not None:
            return config
        return await self.create_default(user_id)

    async def get_or_create_default_for_update(self, user_id: UUID) -> AgentConfig:
        config = await self.get_by_user_id_for_update(user_id)
        if config is not None:
            return config
        return await self.create_default(user_id)

    async def get_by_user_id(self, user_id: UUID | str) -> AgentConfig | None:
        result = await self.session.execute(
            select(AgentConfig).where(AgentConfig.user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def get_by_id(self, agent_config_id: UUID) -> AgentConfig | None:
        return await self.session.get(AgentConfig, agent_config_id)

    async def get_by_id_for_update(self, agent_config_id: UUID) -> AgentConfig | None:
        result = await self.session.execute(
            select(AgentConfig)
            .where(AgentConfig.id == agent_config_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def get_by_user_id_for_update(self, user_id: UUID) -> AgentConfig | None:
        result = await self.session.execute(
            select(AgentConfig)
            .where(AgentConfig.user_id == user_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def update_fields(
        self, config: AgentConfig, updates: dict[str, object]
    ) -> AgentConfig:
        for field, value in updates.items():
            setattr(config, field, value)
        await self.session.flush()
        return config
