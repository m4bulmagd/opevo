import logging
from uuid import UUID
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_config import AgentConfig
from app.repositories.agent_config_repository import AgentConfigRepository
from app.services.onboarding_service import OnboardingService
from app.services.outbox_service import OutboxService
from app.services.subscription_access_policy import SubscriptionAccessPolicy


class AgentConfigNotFoundError(Exception):
    pass


class AgentConfigPhoneNumberNotFoundError(Exception):
    pass


class AgentConfigTelephonySyncError(Exception):
    pass


class AgentConfigReadinessError(Exception):
    pass


logger = logging.getLogger(__name__)


class AgentConfigService:
    def __init__(
        self,
        session: AsyncSession,
        agent_config_repository: AgentConfigRepository,
        onboarding_service: OnboardingService,
        arq_pool=None,
    ) -> None:
        self.session = session
        self.agent_config_repository = agent_config_repository
        self.onboarding_service = onboarding_service
        self.outbox_service = OutboxService(session)
        self.arq_pool = arq_pool

    async def get_by_user_id(self, user_id: UUID) -> AgentConfig:
        return await self.agent_config_repository.get_or_create_default(user_id)

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
                    await self._ensure_ready_to_enable(user_id, config)
                await self.outbox_service.add(
                    topic=("phone.enable" if bool(requested_enabled) else "phone.disable"),
                    aggregate_type="user",
                    aggregate_id=user_id,
                    idempotency_key=(
                        f"agent-config:{config.id}:routing:{uuid4().hex}"
                    ),
                    payload={"user_id": str(user_id)},
                )
            await self.session.commit()
        except ValueError as exc:
            await self.session.rollback()
            raise AgentConfigPhoneNumberNotFoundError from exc
        except AgentConfigReadinessError:
            await self.session.rollback()
            raise
        except AgentConfigPhoneNumberNotFoundError:
            raise
        except Exception as exc:
            await self.session.rollback()
            raise AgentConfigTelephonySyncError from exc

        if should_toggle and self.arq_pool is not None:
            try:
                await self.arq_pool.enqueue_job("outbox_delivery_job", {})
            except Exception as error:
                logger.warning(
                    "outbox wakeup enqueue failed operation=agent_config_routing "
                    "error_type=%s",
                    type(error).__name__,
                )
        await self.session.refresh(config)
        return config

    async def _ensure_ready_to_enable(self, user_id: UUID, config: AgentConfig) -> None:
        status = await self.onboarding_service.get_status(user_id)
        if not SubscriptionAccessPolicy.can_route(
            status.subscription_status or "",
            None,
        ):
            raise AgentConfigReadinessError
        if status.phone_number_status != "ready":
            raise AgentConfigReadinessError
        if not self.onboarding_service._is_agent_setup_complete(config):
            raise AgentConfigReadinessError
