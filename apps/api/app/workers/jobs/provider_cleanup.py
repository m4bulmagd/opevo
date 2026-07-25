from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.core.config import get_settings
from app.core.database import get_session_factory
from app.models.outbox_event import OutboxEvent
from app.providers.subscriptions.base import SubscriptionProviderError
from app.providers.subscriptions.factory import build_subscription_provider
from app.providers.telephony.base import TelephonyProviderError
from app.providers.telephony.factory import create_telephony_provider
from app.repositories.provider_cleanup_repository import ProviderCleanupRepository
from app.workers.jobs.outbox_delivery import OutboxDeliveryError


@dataclass(frozen=True)
class _CleanupSnapshot:
    operation_id: UUID
    resource_type: str
    provider_resource_id: str
    routing_disabled: bool


async def deliver_provider_cleanup(
    ctx: dict[str, Any],
    event: OutboxEvent,
) -> None:
    operation_id = _validated_operation_id(event)
    session_factory = ctx.get("session_factory") or get_session_factory()
    now_provider: Callable[[], datetime] = ctx.get(
        "provider_cleanup_now",
        lambda: datetime.now(UTC),
    )
    snapshot = await _begin_attempt(session_factory, operation_id)
    if snapshot is None:
        return

    if snapshot.resource_type == "phone_number":
        provider = ctx.get("telephony_provider")
        if provider is None:
            provider = create_telephony_provider(get_settings())
        if not snapshot.routing_disabled:
            try:
                result = await provider.disable_number(
                    provider_number_id=snapshot.provider_resource_id
                )
                if result != "app-disabled":
                    raise TelephonyProviderError(
                        "provider_retryable",
                        error_class="conflict",
                    )
            except TelephonyProviderError as error:
                await _record_failure(
                    session_factory,
                    operation_id,
                    error.category,
                )
                raise OutboxDeliveryError(
                    error.category,
                    retryable=error.retryable,
                    exhaustible=not error.retryable,
                ) from None
            await _mark_routing_disabled(
                session_factory,
                operation_id,
                now_provider(),
            )
        try:
            await provider.release_number(
                provider_number_id=snapshot.provider_resource_id
            )
        except TelephonyProviderError as error:
            await _record_failure(
                session_factory,
                operation_id,
                error.category,
            )
            raise OutboxDeliveryError(
                error.category,
                retryable=error.retryable,
                exhaustible=not error.retryable,
            ) from None
    else:
        provider = ctx.get("subscription_provider")
        if provider is None:
            provider = build_subscription_provider(get_settings())
        try:
            await provider.cancel_immediately(snapshot.provider_resource_id)
        except SubscriptionProviderError as error:
            await _record_failure(
                session_factory,
                operation_id,
                error.category,
            )
            raise OutboxDeliveryError(
                error.category,
                retryable=error.retryable,
                exhaustible=not error.retryable,
            ) from None

    await _mark_completed(session_factory, operation_id, now_provider())


def _validated_operation_id(event: OutboxEvent) -> UUID:
    if (
        event.topic != "provider.cleanup"
        or event.aggregate_type != "provider-cleanup-operation"
        or not isinstance(event.payload, dict)
        or set(event.payload) != {"cleanup_operation_id"}
    ):
        raise OutboxDeliveryError("invalid_payload", retryable=False)
    try:
        operation_id = UUID(event.payload["cleanup_operation_id"])
    except (TypeError, ValueError, AttributeError):
        raise OutboxDeliveryError("invalid_payload", retryable=False) from None
    if event.aggregate_id != operation_id:
        raise OutboxDeliveryError("invalid_payload", retryable=False)
    return operation_id


async def _begin_attempt(session_factory, operation_id: UUID) -> _CleanupSnapshot | None:
    async with session_factory() as session:
        operation = await ProviderCleanupRepository(session).get_by_id_for_update(
            operation_id
        )
        if operation is None:
            await session.commit()
            raise OutboxDeliveryError("invalid_payload", retryable=False)
        if operation.completed_at is not None:
            await session.commit()
            return None
        operation.status = "processing"
        operation.attempt_count += 1
        operation.last_error_code = None
        snapshot = _CleanupSnapshot(
            operation_id=operation.id,
            resource_type=operation.resource_type,
            provider_resource_id=operation.provider_resource_id,
            routing_disabled=operation.routing_disabled_at is not None,
        )
        await session.commit()
        return snapshot


async def _mark_routing_disabled(
    session_factory,
    operation_id: UUID,
    now: datetime,
) -> None:
    async with session_factory() as session:
        operation = await ProviderCleanupRepository(session).get_by_id_for_update(
            operation_id
        )
        if operation is not None and operation.routing_disabled_at is None:
            operation.routing_disabled_at = now
        await session.commit()


async def _record_failure(
    session_factory,
    operation_id: UUID,
    error_code: str,
) -> None:
    async with session_factory() as session:
        operation = await ProviderCleanupRepository(session).get_by_id_for_update(
            operation_id
        )
        if operation is not None and operation.completed_at is None:
            operation.status = (
                "pending"
                if error_code == "provider_retryable"
                else "attention_required"
            )
            operation.last_error_code = error_code
        await session.commit()


async def _mark_completed(
    session_factory,
    operation_id: UUID,
    now: datetime,
) -> None:
    async with session_factory() as session:
        operation = await ProviderCleanupRepository(session).get_by_id_for_update(
            operation_id
        )
        if operation is not None and operation.completed_at is None:
            operation.status = "completed"
            operation.completed_at = now
            operation.last_error_code = None
        await session.commit()
