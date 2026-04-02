from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_config import AgentConfig
from app.repositories.agent_config_repository import AgentConfigRepository
from app.repositories.user_repository import UserRepository
from app.services.telephony_service import TelephonyService


class AgentConfigNotFoundError(Exception):
    pass


class AgentConfigPhoneNumberNotFoundError(Exception):
    pass


class AgentConfigTelephonySyncError(Exception):
    pass


class AgentConfigService:
    def __init__(self, session: AsyncSession, telephony_service: TelephonyService | None = None) -> None:
        self.session = session
        self.user_repository = UserRepository(session)
        self.agent_config_repository = AgentConfigRepository(session)
        self.telephony_service = telephony_service or TelephonyService(session)

    async def _get_user_and_config(self, clerk_user_id: str):
        user = await self.user_repository.get_by_clerk_user_id(clerk_user_id)
        if user is None:
            raise AgentConfigNotFoundError

        config = await self.agent_config_repository.get_by_user_id(user.id)
        if config is None:
            raise AgentConfigNotFoundError

        return user, config

    async def get_by_clerk_user_id(self, clerk_user_id: str) -> AgentConfig:
        _, config = await self._get_user_and_config(clerk_user_id)
        return config

    async def update_by_clerk_user_id(
        self, clerk_user_id: str, updates: dict[str, object]
    ) -> AgentConfig:
        user, config = await self._get_user_and_config(clerk_user_id)
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
                    await self.telephony_service.enable_number(user.id, commit=False)
                else:
                    await self.telephony_service.disable_number(user.id, commit=False)
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
