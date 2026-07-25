import logging
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account_deactivation_operation import (
    AccountDeactivationOperation,
    DeactivationTrigger,
)
from app.repositories.account_deactivation_repository import (
    AccountDeactivationRepository,
)
from app.repositories.agent_config_repository import AgentConfigRepository
from app.repositories.phone_number_repository import PhoneNumberRepository
from app.repositories.subscription_repository import SubscriptionRepository
from app.repositories.user_repository import UserRepository
from app.schemas.account import (
    AccountStatusResponse,
    DeactivationProgressResponse,
)
from app.services.customer_readiness_service import CustomerReadinessService
from app.services.outbox_service import OutboxService


logger = logging.getLogger(__name__)


class AccountLifecycleService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        account_deactivation_repository: AccountDeactivationRepository | None = None,
        agent_config_repository: AgentConfigRepository | None = None,
        phone_number_repository: PhoneNumberRepository | None = None,
        readiness_service: CustomerReadinessService | None = None,
        subscription_repository: SubscriptionRepository | None = None,
        outbox_service: OutboxService | None = None,
        user_repository: UserRepository | None = None,
        arq_pool=None,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self.session = session
        self.account_deactivation_repository = (
            account_deactivation_repository or AccountDeactivationRepository(session)
        )
        self.agent_config_repository = (
            agent_config_repository or AgentConfigRepository(session)
        )
        self.phone_number_repository = (
            phone_number_repository or PhoneNumberRepository(session)
        )
        self.readiness_service = readiness_service or CustomerReadinessService(session)
        self.subscription_repository = (
            subscription_repository or SubscriptionRepository(session)
        )
        self.outbox_service = outbox_service or OutboxService(session)
        self.user_repository = user_repository or UserRepository(session)
        self.arq_pool = arq_pool
        self.now = now_provider or (lambda: datetime.now(UTC))

    async def get_account(self, user_id: UUID) -> AccountStatusResponse:
        user = await self.user_repository.get_by_id(user_id)
        if user is None:
            raise ValueError("Account not found")

        if user.status == "active":
            readiness = await self.readiness_service.evaluate(user_id)
            serving = readiness.result.can_route
            return AccountStatusResponse(
                status="active",
                serving=serving,
                deactivation=None,
                reactivation_allowed=False,
                blocker=None if serving else "customer_not_ready",
            )

        operation = await self.account_deactivation_repository.get_latest_by_user_id(
            user_id
        )
        progress = self._progress(operation)
        if user.status == "deactivating":
            return AccountStatusResponse(
                status="deactivating",
                serving=False,
                deactivation=progress,
                reactivation_allowed=False,
                blocker=(
                    "deactivation_attention_required"
                    if progress is not None and progress.state == "attention_required"
                    else "account_deactivating"
                ),
            )

        phone_number = await self.phone_number_repository.get_by_user_id(user_id)
        incomplete = operation is not None and operation.completed_at is None
        return AccountStatusResponse(
            status="inactive",
            serving=False,
            deactivation=progress if incomplete else None,
            reactivation_allowed=not incomplete and phone_number is None,
            blocker=(
                "deactivation_attention_required"
                if progress is not None and progress.state == "attention_required"
                else "account_inactive"
            ),
        )

    async def request_owner_deactivation(
        self,
        user_id: UUID,
        confirmation: str,
    ) -> AccountStatusResponse:
        if confirmation != "DEACTIVATE":
            raise ValueError("Invalid deactivation confirmation")
        try:
            await self.request_in_transaction(user_id, trigger="owner_request")
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            raise

        await self._wake_outbox()
        return await self.get_account(user_id)

    async def request_in_transaction(
        self,
        user_id: UUID,
        trigger: DeactivationTrigger,
        stripe_subscription_id: str | None = None,
    ) -> AccountDeactivationOperation | None:
        user = await self.user_repository.get_by_id_for_update(user_id)
        if user is None:
            return None

        subscription = await self.subscription_repository.get_by_user_id_for_update(
            user_id
        )
        phone_number = await self.phone_number_repository.get_by_user_id_for_update(
            user_id
        )
        config = await self.agent_config_repository.get_by_user_id_for_update(user_id)
        incomplete = (
            await self.account_deactivation_repository.get_incomplete_by_user_id_for_update(
                user_id
            )
        )
        if incomplete is not None:
            return incomplete

        latest = await self.account_deactivation_repository.get_latest_by_user_id(user_id)
        if user.status == "inactive":
            if (
                latest is not None
                and latest.completed_at is not None
                and latest.stripe_subscription_id == stripe_subscription_id
            ):
                return latest
            return None

        if (
            trigger == "subscription_ended"
            and (
                subscription is None
                or subscription.stripe_subscription_id != stripe_subscription_id
            )
        ):
            return None

        requested_at = self.now()
        current_subscription_id = (
            subscription.stripe_subscription_id if subscription is not None else None
        )
        phone_provider_id = (
            phone_number.provider_number_id if phone_number is not None else None
        )

        await self.user_repository.start_deactivation(user)
        if config is not None:
            await self.agent_config_repository.disable_for_deactivation(config)
        if phone_number is not None:
            phone_number.is_active = False
            await self.session.flush()

        operation = await self.account_deactivation_repository.create(
            user_id=user_id,
            lifecycle_generation=user.lifecycle_generation,
            trigger=trigger,
            requested_at=requested_at,
            stripe_subscription_id=current_subscription_id,
            phone_provider_id=phone_provider_id,
        )
        await self.outbox_service.add(
            topic="account.deactivate",
            aggregate_type="account-deactivation-operation",
            aggregate_id=operation.id,
            idempotency_key=f"account.deactivate:{operation.id}",
            payload={"operation_id": str(operation.id)},
        )
        return operation

    @staticmethod
    def _progress(
        operation: AccountDeactivationOperation | None,
    ) -> DeactivationProgressResponse | None:
        if operation is None or operation.completed_at is not None:
            return None
        if operation.status == "attention_required":
            state = "attention_required"
        elif operation.status == "pending":
            state = "requested"
        elif operation.routing_disabled_at is None:
            state = "disabling_routing"
        elif operation.subscription_canceled_at is None:
            state = "canceling_subscription"
        elif operation.active_call_drained_at is None:
            state = "draining_call"
        elif operation.number_released_at is None:
            state = "releasing_number"
        elif operation.activation_reset_at is None:
            state = "finalizing"
        else:
            state = "finalizing"
        return DeactivationProgressResponse(state=state, requested_at=operation.requested_at)

    async def _wake_outbox(self) -> None:
        if self.arq_pool is None:
            return
        try:
            await self.arq_pool.enqueue_job("outbox_delivery_job", {})
        except Exception:
            logger.warning("outbox wakeup enqueue failed operation=deactivate_account")
