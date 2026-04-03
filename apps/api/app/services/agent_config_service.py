from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_config import AgentConfig
from app.repositories.agent_config_repository import AgentConfigRepository
from app.services.telephony_service import TelephonyService


class AgentConfigNotFoundError(Exception):
    pass


class AgentConfigPhoneNumberNotFoundError(Exception):
    pass


class AgentConfigTelephonySyncError(Exception):
    pass


class AgentConfigService:
    def __init__(
        self,
        session: AsyncSession,
        agent_config_repository: AgentConfigRepository,
        telephony_service: TelephonyService,
    ) -> None:
        self.session = session
        self.agent_config_repository = agent_config_repository
        self.telephony_service = telephony_service

    async def get_by_user_id(self, user_id: UUID) -> AgentConfig:
        config = await self.agent_config_repository.get_by_user_id(user_id)
        if config is None:
            raise AgentConfigNotFoundError
        return config

    async def update_by_user_id(
        self, user_id: UUID, updates: dict[str, object]
    ) -> AgentConfig:
        config = await self.get_by_user_id(user_id)
        if not updates:
            return config

        requested_enabled = updates.get("is_enabled")
        should_toggle = (
            requested_enabled is not None
            and bool(requested_enabled) != config.is_enabled
        )

        try:
            config = await self.agent_config_repository.update_fields(config, updates)
            if should_toggle:
                if bool(requested_enabled):
                    await self.telephony_service.enable_number(user_id)
                else:
                    await self.telephony_service.disable_number(user_id)
            await self.session.commit()
        except ValueError as exc:
            await self.session.rollback()
            raise AgentConfigPhoneNumberNotFoundError from exc
        except AgentConfigPhoneNumberNotFoundError:
            raise
        except Exception as exc:
            await self.session.rollback()
            raise AgentConfigTelephonySyncError from exc

        await self.session.refresh(config)
        return config
