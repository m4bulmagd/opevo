from uuid import UUID

from app.models.outbox_event import OutboxEvent
from app.repositories.user_repository import UserRepository
from app.services.account_access_policy import (
    AccountLifecycleGenerationMismatchError,
    AccountStateBlockedError,
    require_current_account_lifecycle,
)
from app.workers.outbox.failures import OutboxDeliveryError


def _validated_lifecycle_generation(event: OutboxEvent) -> int:
    lifecycle_generation = event.payload.get("lifecycle_generation")
    if type(lifecycle_generation) is not int or lifecycle_generation < 1:
        raise OutboxDeliveryError(
            "dispatch_configuration",
            retryable=False,
        )
    return lifecycle_generation


async def _require_current_worker_account(
    session_factory,
    user_id: UUID,
    *,
    lifecycle_generation: int,
) -> None:
    async with session_factory() as session:
        user = await UserRepository(session).get_by_id_for_update(user_id)
        if user is None:
            await session.rollback()
            raise OutboxDeliveryError(
                "dispatch_ineligible",
                retryable=False,
            )
        try:
            require_current_account_lifecycle(
                user,
                lifecycle_generation=lifecycle_generation,
            )
        except (AccountStateBlockedError, AccountLifecycleGenerationMismatchError):
            await session.rollback()
            raise OutboxDeliveryError(
                "dispatch_ineligible",
                retryable=False,
            ) from None
        await session.commit()
