from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.outbox_event import OutboxEvent
from app.models.provider_cleanup_operation import ProviderCleanupOperation
from app.models.user import User
from app.core.provider_failures import ProviderFailure
from app.repositories.provider_cleanup_repository import ProviderCleanupRepository
from app.services.outbox_service import OutboxService
from app.workers.outbox.failures import OutboxDeliveryError
from app.workers.outbox.provider_cleanup import deliver_provider_cleanup


class RecordingTelephony:
    def __init__(self, calls: list[str], *, fail_release: bool = False) -> None:
        self.calls = calls
        self.fail_release = fail_release

    async def disable_number(self, *, provider_number_id: str) -> str:
        self.calls.append(f"disable:{provider_number_id}")
        return "app-disabled"

    async def release_number(self, *, provider_number_id: str) -> None:
        self.calls.append(f"release:{provider_number_id}")
        if self.fail_release:
            raise ProviderFailure(
                provider="telnyx",
                operation="release_number",
                disposition="retryable",
                error_class="timeout",
            )


class RecordingSubscriptions:
    def __init__(self, calls: list[str], *, fail: bool = False) -> None:
        self.calls = calls
        self.fail = fail

    async def cancel_immediately(self, subscription_id: str) -> None:
        self.calls.append(f"cancel:{subscription_id}")
        if self.fail:
            raise ProviderFailure(
                provider="stripe",
                operation="cancel_subscription",
                disposition="retryable",
                error_class="unavailable",
            )


async def _seed_cleanup(db_session, active_user: User, resource_type: str):
    operation = await ProviderCleanupRepository(db_session).adopt(
        user_id=active_user.id,
        lifecycle_generation=active_user.lifecycle_generation,
        resource_type=resource_type,
        provider_resource_id=(
            "pn-private-cleanup"
            if resource_type == "phone_number"
            else "sub-private-cleanup"
        ),
    )
    event = await OutboxService(db_session).add(
        topic="provider.cleanup",
        aggregate_type="provider-cleanup-operation",
        aggregate_id=operation.id,
        idempotency_key=f"provider.cleanup:{operation.id}",
        payload={"cleanup_operation_id": str(operation.id)},
    )
    await db_session.commit()
    return operation.id, event


@pytest.mark.anyio
async def test_phone_cleanup_commits_disable_before_release_and_is_replay_safe(
    db_session,
    active_user,
) -> None:
    operation_id, event = await _seed_cleanup(
        db_session,
        active_user,
        "phone_number",
    )
    calls: list[str] = []
    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    ctx = {
        "session_factory": session_factory,
        "telephony_provider": RecordingTelephony(calls),
        "provider_cleanup_now": lambda: datetime.now(UTC),
    }

    await deliver_provider_cleanup(ctx, event)
    await deliver_provider_cleanup(ctx, event)

    stored = await db_session.get(ProviderCleanupOperation, operation_id)
    await db_session.refresh(stored)
    assert calls == [
        "disable:pn-private-cleanup",
        "release:pn-private-cleanup",
    ]
    assert stored is not None
    assert stored.status == "completed"
    assert stored.routing_disabled_at is not None
    assert stored.completed_at is not None


@pytest.mark.anyio
async def test_phone_cleanup_retries_release_without_repeating_committed_disable(
    db_session,
    active_user,
) -> None:
    operation_id, event = await _seed_cleanup(
        db_session,
        active_user,
        "phone_number",
    )
    calls: list[str] = []
    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    failing = RecordingTelephony(calls, fail_release=True)
    ctx = {
        "session_factory": session_factory,
        "telephony_provider": failing,
        "provider_cleanup_now": lambda: datetime.now(UTC),
    }

    with pytest.raises(OutboxDeliveryError) as raised:
        await deliver_provider_cleanup(ctx, event)
    assert raised.value.retryable is True
    failing.fail_release = False
    await deliver_provider_cleanup(ctx, event)

    stored = await db_session.get(ProviderCleanupOperation, operation_id)
    await db_session.refresh(stored)
    assert calls == [
        "disable:pn-private-cleanup",
        "release:pn-private-cleanup",
        "release:pn-private-cleanup",
    ]
    assert stored is not None
    assert stored.status == "completed"


@pytest.mark.anyio
async def test_stale_subscription_cleanup_retries_then_cancels_once(
    db_session,
    active_user,
) -> None:
    operation_id, event = await _seed_cleanup(
        db_session,
        active_user,
        "stripe_subscription",
    )
    calls: list[str] = []
    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    provider = RecordingSubscriptions(calls, fail=True)
    ctx = {
        "session_factory": session_factory,
        "subscription_provider": provider,
        "provider_cleanup_now": lambda: datetime.now(UTC),
    }

    with pytest.raises(OutboxDeliveryError):
        await deliver_provider_cleanup(ctx, event)
    provider.fail = False
    await deliver_provider_cleanup(ctx, event)
    await deliver_provider_cleanup(ctx, event)

    stored = await db_session.get(ProviderCleanupOperation, operation_id)
    await db_session.refresh(stored)
    assert calls == [
        "cancel:sub-private-cleanup",
        "cancel:sub-private-cleanup",
    ]
    assert stored is not None
    assert stored.status == "completed"
    outbox = await db_session.scalar(
        select(OutboxEvent).where(OutboxEvent.aggregate_id == operation_id)
    )
    assert outbox is not None
    assert outbox.payload == {"cleanup_operation_id": str(operation_id)}
