import logging
from uuid import UUID
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.agent_config import AgentConfig
from app.repositories.agent_config_repository import AgentConfigRepository
from app.repositories.user_repository import UserRepository
from app.services.account_access_policy import (
    AccountStateBlockedError,
    require_active_account,
)
from app.services.customer_readiness_policy import ReadinessBlocker
from app.services.customer_readiness_service import CustomerReadinessService
from app.services.outbox_service import OutboxService


class AgentConfigNotFoundError(Exception):
    pass


class AgentConfigPhoneNumberNotFoundError(Exception):
    pass


class AgentConfigTelephonySyncError(Exception):
    pass


class AgentConfigContentManagedError(Exception):
    pass


class AgentConfigEnableManagedByActivationError(Exception):
    pass


class AgentConfigReadinessError(Exception):
    def __init__(self, blockers: tuple[str, ...]) -> None:
        super().__init__("Agent configuration is not ready to enable")
        self.blockers = blockers


logger = logging.getLogger(__name__)
PROFILE_MANAGED_CONTENT_FIELDS = frozenset(
    {"agent_name", "owner_context", "system_prompt", "knowledge_base"}
)


class AgentConfigService:
    def __init__(
        self,
        session: AsyncSession,
        agent_config_repository: AgentConfigRepository,
        readiness_service: CustomerReadinessService,
        arq_pool=None,
    ) -> None:
        self.session = session
        self.agent_config_repository = agent_config_repository
        self.user_repository = UserRepository(session)
        self.readiness_service = readiness_service
        self.outbox_service = OutboxService(session)
        self.arq_pool = arq_pool

    async def get_by_user_id(self, user_id: UUID) -> AgentConfig:
        return await self.agent_config_repository.get_or_create_default(user_id)

    async def update_by_user_id(
        self,
        user_id: UUID,
        updates: dict[str, object],
        *,
        requested_fields: set[str] | None = None,
    ) -> AgentConfig:
        user = await self.user_repository.get_by_id_for_update(user_id)
        if user is None:
            await self.session.rollback()
            raise AgentConfigNotFoundError
        try:
            require_active_account(user)
        except AccountStateBlockedError:
            await self.session.rollback()
            raise
        if (
            get_settings().activation_flow_enabled
            and "is_enabled"
            in (requested_fields if requested_fields is not None else updates.keys())
            and updates.get("is_enabled") is True
        ):
            raise AgentConfigEnableManagedByActivationError
        if get_settings().activation_flow_enabled and PROFILE_MANAGED_CONTENT_FIELDS & (
            requested_fields if requested_fields is not None else updates.keys()
        ):
            raise AgentConfigContentManagedError
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
                    topic=(
                        "phone.enable" if bool(requested_enabled) else "phone.disable"
                    ),
                    aggregate_type="user",
                    aggregate_id=user_id,
                    idempotency_key=(f"agent-config:{config.id}:routing:{uuid4().hex}"),
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
        context = await self.readiness_service.evaluate(
            user_id,
            agent_config_override=config,
        )
        if context.result.can_activate:
            return

        projection_blockers = {
            ReadinessBlocker.AGENT_DISABLED,
            ReadinessBlocker.PHONE_INACTIVE,
            ReadinessBlocker.PHONE_PROJECTION_INACTIVE,
        }
        activation_blockers = tuple(
            blocker.value
            for blocker in context.result.blockers
            if blocker not in projection_blockers
        )
        if not activation_blockers:
            raise RuntimeError("readiness denied activation without a blocker")
        raise AgentConfigReadinessError(activation_blockers)
