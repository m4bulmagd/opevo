import asyncio
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.models import Base
from app.models.outbox_event import OutboxEvent
from app.models.agent_config import AgentConfig
from app.models.phone_number import PhoneNumber
from app.models.phone_number_provisioning import PhoneNumberProvisioning
from app.models.subscription import Subscription
from app.models.user import User
from app.models.usage_ledger import UsageLedger
from app.repositories.outbox_repository import OutboxRepository
from app.services.outbox_service import OutboxService
from app.services.onboarding_service import OnboardingRetryNotAllowedError, OnboardingService


@pytest_asyncio.fixture
async def outbox_session_factory() -> AsyncIterator[
    async_sessionmaker[AsyncSession]
]:
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("PostgreSQL outbox tests require TEST_DATABASE_URL")
    if database_url.startswith("postgresql://"):
        database_url = database_url.replace(
            "postgresql://", "postgresql+asyncpg://", 1
        )
    if not database_url.startswith("postgresql+asyncpg://"):
        pytest.skip("TEST_DATABASE_URL must identify a PostgreSQL database")

    schema_name = f"task7_outbox_{uuid4().hex}"
    quoted_schema = f'"{schema_name}"'
    admin_engine = create_async_engine(database_url, isolation_level="AUTOCOMMIT")
    test_engine = None
    try:
        async with admin_engine.connect() as connection:
            await connection.execute(text(f"CREATE SCHEMA {quoted_schema}"))

        test_engine = create_async_engine(
            database_url,
            connect_args={"server_settings": {"search_path": schema_name}},
        )
        async with test_engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        yield async_sessionmaker(test_engine, expire_on_commit=False)
    finally:
        if test_engine is not None:
            await test_engine.dispose()
        async with admin_engine.connect() as connection:
            await connection.execute(
                text(f"DROP SCHEMA IF EXISTS {quoted_schema} CASCADE")
            )
        await admin_engine.dispose()


async def _add_event(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    topic: str = "phone.disable",
    idempotency_key: str | None = None,
    aggregate_id=None,
    payload: dict | None = None,
) -> OutboxEvent:
    async with session_factory() as session:
        event = await OutboxService(session).add(
            topic=topic,
            aggregate_type="user",
            aggregate_id=aggregate_id or uuid4(),
            idempotency_key=idempotency_key or f"test:{uuid4().hex}",
            payload=payload or {"user_id": str(uuid4())},
        )
        await session.commit()
        return event


@pytest.mark.anyio
async def test_outbox_event_rolls_back_with_business_transaction(
    outbox_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with outbox_session_factory() as session:
        await OutboxService(session).add(
            topic="phone.disable",
            aggregate_type="subscription",
            aggregate_id=uuid4(),
            idempotency_key="stripe:sub_updated:evt_123",
            payload={"user_id": str(uuid4())},
        )
        await session.rollback()

        assert await session.scalar(
            select(func.count()).select_from(OutboxEvent)
        ) == 0


@pytest.mark.anyio
async def test_two_workers_claim_one_due_event_once(
    outbox_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    event = await _add_event(outbox_session_factory)
    now = datetime.now(UTC)

    async def claim() -> list:
        async with outbox_session_factory() as session:
            claimed = await OutboxRepository(session).claim_batch(limit=1, now=now)
            await session.commit()
            return claimed

    first, second = await asyncio.gather(claim(), claim())

    assert sum(len(batch) for batch in (first, second)) == 1
    assert {item.id for batch in (first, second) for item in batch} == {event.id}
    assert [item.attempt_count for batch in (first, second) for item in batch] == [1]


@pytest.mark.anyio
async def test_concurrent_add_once_returns_one_durable_identity(
    outbox_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    aggregate_id = uuid4()
    user_id = uuid4()

    async def add() -> object:
        async with outbox_session_factory() as session:
            event = await OutboxService(session).add(
                topic="phone.disable",
                aggregate_type="subscription",
                aggregate_id=aggregate_id,
                idempotency_key="concurrent:add-once",
                payload={"user_id": str(user_id)},
            )
            await session.commit()
            return event.id

    first_id, second_id = await asyncio.gather(add(), add())

    assert first_id == second_id
    async with outbox_session_factory() as session:
        assert await session.scalar(
            select(func.count()).select_from(OutboxEvent)
        ) == 1


@pytest.mark.anyio
async def test_concurrent_onboarding_retry_creates_one_new_attempt(
    outbox_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with outbox_session_factory() as session:
        user = User(
            clerk_user_id=f"retry_{uuid4().hex}",
            email=f"retry_{uuid4().hex}@example.com",
        )
        session.add(user)
        await session.flush()
        session.add(
            Subscription(
                user_id=user.id,
                stripe_customer_id=f"cus_{uuid4().hex}",
                stripe_subscription_id=f"sub_{uuid4().hex}",
                plan_tier="starter",
                status="active",
                allocated_minutes=60,
            )
        )
        session.add(
            PhoneNumberProvisioning(
                user_id=user.id,
                target_country_code="FR",
                status="failed",
                attempt_count=3,
                can_retry=True,
            )
        )
        await session.commit()
        user_id = user.id

    async def retry() -> str:
        async with outbox_session_factory() as session:
            try:
                await OnboardingService(session).retry_provisioning(
                    user_id,
                    arq_pool=None,
                )
            except OnboardingRetryNotAllowedError:
                return "rejected"
            return "accepted"

    outcomes = await asyncio.gather(retry(), retry())

    assert sorted(outcomes) == ["accepted", "rejected"]
    async with outbox_session_factory() as session:
        provisioning = await session.scalar(
            select(PhoneNumberProvisioning).where(
                PhoneNumberProvisioning.user_id == user_id
            )
        )
        events = list((await session.execute(select(OutboxEvent))).scalars())
        assert provisioning is not None
        assert provisioning.status == "queued"
        assert provisioning.attempt_count == 3
        assert len(events) == 1


@pytest.mark.anyio
async def test_expired_processing_lease_is_reclaimed_after_worker_crash(
    outbox_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    event = await _add_event(outbox_session_factory)
    claimed_at = datetime.now(UTC)

    async with outbox_session_factory() as session:
        assert len(
            await OutboxRepository(session).claim_batch(limit=1, now=claimed_at)
        ) == 1
        await session.commit()

    async with outbox_session_factory() as session:
        before_expiry = await OutboxRepository(session).claim_batch(
            limit=1,
            now=claimed_at + timedelta(minutes=4, seconds=59),
        )
        await session.commit()
    assert before_expiry == []

    async with outbox_session_factory() as session:
        after_expiry = await OutboxRepository(session).claim_batch(
            limit=1,
            now=claimed_at + timedelta(minutes=5),
        )
        await session.commit()

    assert [item.id for item in after_expiry] == [event.id]
    assert after_expiry[0].attempt_count == 2


@pytest.mark.anyio
async def test_reclaimed_event_rejects_stale_worker_completion(
    outbox_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    event = await _add_event(outbox_session_factory)
    claimed_at = event.next_attempt_at + timedelta(seconds=1)

    async with outbox_session_factory() as session:
        first_claim = (
            await OutboxRepository(session).claim_batch(limit=1, now=claimed_at)
        )[0]
        first_generation = first_claim.attempt_count
        await session.commit()

    reclaimed_at = claimed_at + timedelta(minutes=5)
    async with outbox_session_factory() as session:
        second_claim = (
            await OutboxRepository(session).claim_batch(limit=1, now=reclaimed_at)
        )[0]
        second_generation = second_claim.attempt_count
        await session.commit()

    async with outbox_session_factory() as session:
        stale_result = await OutboxRepository(session).mark_delivered(
            event_id=event.id,
            attempt_count=first_generation,
            delivered_at=reclaimed_at + timedelta(seconds=1),
        )
        await session.commit()
    assert stale_result is None

    async with outbox_session_factory() as session:
        stored = await session.get(OutboxEvent, event.id)
        assert stored is not None
        assert stored.status == "processing"
        assert stored.attempt_count == second_generation == 2


@pytest.mark.anyio
async def test_claim_preserves_aggregate_order(
    outbox_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    aggregate_id = uuid4()
    first = await _add_event(
        outbox_session_factory,
        aggregate_id=aggregate_id,
        idempotency_key="aggregate:first",
    )
    second = await _add_event(
        outbox_session_factory,
        aggregate_id=aggregate_id,
        idempotency_key="aggregate:second",
    )
    now = second.next_attempt_at + timedelta(seconds=1)

    async with outbox_session_factory() as session:
        claimed = await OutboxRepository(session).claim_batch(limit=10, now=now)
        await session.commit()

    assert [item.id for item in claimed] == [first.id]


@pytest.mark.anyio
async def test_terminal_failure_does_not_block_later_same_user_event(
    outbox_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    from app.workers.jobs.outbox_delivery import outbox_delivery_job

    aggregate_id = uuid4()
    first = await _add_event(
        outbox_session_factory,
        aggregate_id=aggregate_id,
        idempotency_key="aggregate:terminal-first",
    )
    second = await _add_event(
        outbox_session_factory,
        aggregate_id=aggregate_id,
        idempotency_key="aggregate:corrective-second",
    )
    now = datetime.now(UTC) + timedelta(seconds=1)
    async with outbox_session_factory() as session:
        failed = await session.get(OutboxEvent, first.id)
        corrective = await session.get(OutboxEvent, second.id)
        assert failed is not None
        assert corrective is not None
        failed.status = "failed"
        failed.last_error_code = "provider_terminal"
        failed.created_at = now - timedelta(seconds=2)
        corrective.created_at = now - timedelta(seconds=1)
        corrective.next_attempt_at = now - timedelta(milliseconds=1)
        await session.commit()

    delivered: list[UUID] = []

    async def handler(_ctx: dict, event: OutboxEvent) -> None:
        delivered.append(event.id)

    result = await outbox_delivery_job(
        {
            "session_factory": outbox_session_factory,
            "outbox_handlers": {"phone.disable": handler},
            "outbox_now": lambda: now,
        }
    )

    assert result == {"claimed": 1, "delivered": 1, "retried": 0, "failed": 0}
    assert delivered == [second.id]
    async with outbox_session_factory() as session:
        stored = await session.get(OutboxEvent, second.id)
        assert stored is not None
        assert stored.status == "delivered"


@pytest.mark.anyio
async def test_delivery_retries_all_backoffs_then_fails_terminally_with_safe_code(
    outbox_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    from app.workers.jobs.outbox_delivery import (
        OUTBOX_RETRY_DELAYS,
        outbox_delivery_job,
    )

    event = await _add_event(outbox_session_factory)
    current_time = event.next_attempt_at + timedelta(seconds=1)
    metric_calls: list[tuple[str, str]] = []

    async def failing_handler(_ctx: dict, _event: OutboxEvent) -> None:
        raise RuntimeError("RAW_PROVIDER_RESPONSE_MUST_NOT_BE_PERSISTED")

    async def metric(topic: str, error_code: str) -> None:
        metric_calls.append((topic, error_code))

    ctx = {
        "session_factory": outbox_session_factory,
        "outbox_handlers": {"phone.disable": failing_handler},
        "outbox_now": lambda: current_time,
        "outbox_terminal_failure_metric": metric,
    }

    for expected_attempt, delay in enumerate(OUTBOX_RETRY_DELAYS, start=1):
        result = await outbox_delivery_job(ctx)
        assert result == {"claimed": 1, "delivered": 0, "retried": 1, "failed": 0}
        async with outbox_session_factory() as session:
            stored = await session.get(OutboxEvent, event.id)
            assert stored is not None
            assert stored.status == "pending"
            assert stored.attempt_count == expected_attempt
            assert stored.next_attempt_at == current_time + delay
            assert stored.last_error_code == "provider_retryable"
        current_time += delay

    result = await outbox_delivery_job(ctx)
    assert result == {"claimed": 1, "delivered": 0, "retried": 0, "failed": 1}

    async with outbox_session_factory() as session:
        stored = await session.get(OutboxEvent, event.id)
        assert stored is not None
        assert stored.status == "failed"
        assert stored.attempt_count == 6
        assert stored.last_error_code == "provider_retryable"
    assert metric_calls == [("phone.disable", "provider_retryable")]


@pytest.mark.anyio
async def test_delivered_event_is_not_repeated_by_harmless_duplicate_wakeup(
    outbox_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    from app.workers.jobs.outbox_delivery import outbox_delivery_job

    await _add_event(outbox_session_factory)
    delivery_keys: list[str] = []

    async def handler(_ctx: dict, event: OutboxEvent) -> None:
        delivery_keys.append(event.idempotency_key)

    ctx = {
        "session_factory": outbox_session_factory,
        "outbox_handlers": {"phone.disable": handler},
    }

    first = await outbox_delivery_job(ctx)
    second = await outbox_delivery_job(ctx)

    assert first["delivered"] == 1
    assert second["claimed"] == 0
    assert len(delivery_keys) == 1


@pytest.mark.anyio
async def test_provider_handler_runs_after_claim_transaction_releases_row_lock(
    outbox_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    from app.workers.jobs.outbox_delivery import outbox_delivery_job

    event = await _add_event(outbox_session_factory)

    async def handler(_ctx: dict, item: OutboxEvent) -> None:
        async with outbox_session_factory() as probe_session:
            locked = await probe_session.scalar(
                select(OutboxEvent)
                .where(OutboxEvent.id == item.id)
                .with_for_update(nowait=True)
            )
            assert locked is not None
            await probe_session.rollback()

    result = await outbox_delivery_job(
        {
            "session_factory": outbox_session_factory,
            "outbox_handlers": {"phone.disable": handler},
        }
    )

    assert result["delivered"] == 1


@pytest.mark.anyio
async def test_unknown_topic_is_terminal_without_provider_retry(
    outbox_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    from app.workers.jobs.outbox_delivery import outbox_delivery_job

    async with outbox_session_factory() as session:
        event = OutboxEvent(
            topic="unknown.provider.topic",
            aggregate_type="user",
            aggregate_id=uuid4(),
            idempotency_key="unknown-topic",
            payload={"user_id": str(uuid4())},
            next_attempt_at=datetime.now(UTC),
        )
        session.add(event)
        await session.commit()

    async def unrelated_handler(_ctx: dict, _event: OutboxEvent) -> None:
        raise AssertionError("unknown topic must not run a provider")

    result = await outbox_delivery_job(
        {
            "session_factory": outbox_session_factory,
            "outbox_handlers": {"phone.disable": unrelated_handler},
        }
    )

    assert result["failed"] == 1
    assert result["retried"] == 0
    async with outbox_session_factory() as session:
        stored = await session.get(OutboxEvent, event.id)
        assert stored is not None
        assert stored.status == "failed"
        assert stored.attempt_count == 1
        assert stored.last_error_code == "unsupported_topic"


@pytest.mark.anyio
async def test_reconciliation_sweep_recovers_committed_event_without_wakeup(
    outbox_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    from app.workers.jobs.outbox_delivery import outbox_reconciliation_job

    event = await _add_event(outbox_session_factory)
    calls: list[str] = []

    async def handler(_ctx: dict, item: OutboxEvent) -> None:
        calls.append(item.idempotency_key)

    result = await outbox_reconciliation_job(
        {
            "session_factory": outbox_session_factory,
            "outbox_handlers": {"phone.disable": handler},
        }
    )

    assert result["delivered"] == 1
    assert calls == [event.idempotency_key]


class _CapturingRoutingProvider:
    def __init__(self) -> None:
        self.enabled: list[str] = []
        self.disabled: list[str] = []

    async def provision_number(self, **_kwargs):
        raise AssertionError("routing delivery must not provision")

    async def enable_number(self, *, provider_number_id: str) -> str:
        self.enabled.append(provider_number_id)
        return "app-active"

    async def disable_number(self, *, provider_number_id: str) -> str:
        self.disabled.append(provider_number_id)
        return "app-disabled"


async def _seed_routing_state(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    subscription_status: str,
    phone_active: bool,
) -> UUID:
    async with session_factory() as session:
        user = User(
            clerk_user_id=f"routing_{uuid4().hex}",
            email=f"routing_{uuid4().hex}@example.com",
        )
        session.add(user)
        await session.flush()
        session.add(
            Subscription(
                user_id=user.id,
                stripe_customer_id=f"cus_{uuid4().hex}",
                stripe_subscription_id=f"sub_{uuid4().hex}",
                plan_tier="starter",
                status=subscription_status,
                allocated_minutes=60,
            )
        )
        session.add(
            UsageLedger(
                user_id=user.id,
                event_type="subscription_activated",
                minutes_delta=10,
                balance_after=10,
            )
        )
        session.add(
            AgentConfig(
                user_id=user.id,
                agent_name="Presvo Front Desk",
                owner_context="Dental office reception",
                system_prompt="Handle calls professionally.",
                knowledge_base="Open weekdays.",
                pipeline_mode="stt_llm_tts",
                is_enabled=True,
            )
        )
        session.add(
            PhoneNumber(
                user_id=user.id,
                e164=f"+33{str(user.id.int)[-9:]}",
                country_code="FR",
                provider="telnyx",
                provider_number_id=f"pn_{user.id.hex}",
                provider_connection_name=(
                    "app-active" if phone_active else "app-disabled"
                ),
                is_active=phone_active,
            )
        )
        await session.commit()
        return user.id


@pytest.mark.anyio
async def test_stale_enable_after_cancellation_converges_to_disabled_current_state(
    outbox_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    from app.workers.jobs.outbox_delivery import outbox_delivery_job

    user_id = await _seed_routing_state(
        outbox_session_factory,
        subscription_status="canceled",
        phone_active=True,
    )
    await _add_event(
        outbox_session_factory,
        topic="phone.enable",
        aggregate_id=user_id,
        payload={"user_id": str(user_id)},
    )
    provider = _CapturingRoutingProvider()

    result = await outbox_delivery_job(
        {
            "session_factory": outbox_session_factory,
            "telephony_provider": provider,
        }
    )

    assert result["delivered"] == 1
    assert provider.enabled == []
    assert len(provider.disabled) == 1
    async with outbox_session_factory() as session:
        phone = await session.scalar(
            select(PhoneNumber).where(PhoneNumber.user_id == user_id)
        )
        assert phone is not None
        assert phone.is_active is False


@pytest.mark.anyio
async def test_stale_disable_after_reactivation_converges_to_enabled_current_state(
    outbox_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    from app.workers.jobs.outbox_delivery import outbox_delivery_job

    user_id = await _seed_routing_state(
        outbox_session_factory,
        subscription_status="active",
        phone_active=False,
    )
    await _add_event(
        outbox_session_factory,
        topic="phone.disable",
        aggregate_id=user_id,
        payload={"user_id": str(user_id)},
    )
    provider = _CapturingRoutingProvider()

    result = await outbox_delivery_job(
        {
            "session_factory": outbox_session_factory,
            "telephony_provider": provider,
        }
    )

    assert result["delivered"] == 1
    assert provider.disabled == []
    assert len(provider.enabled) == 1
    async with outbox_session_factory() as session:
        phone = await session.scalar(
            select(PhoneNumber).where(PhoneNumber.user_id == user_id)
        )
        assert phone is not None
        assert phone.is_active is True


@pytest.mark.anyio
async def test_disable_replay_reapplies_provider_when_projection_already_disabled(
    outbox_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    from app.workers.jobs.outbox_delivery import outbox_delivery_job

    user_id = await _seed_routing_state(
        outbox_session_factory,
        subscription_status="canceled",
        phone_active=False,
    )
    await _add_event(
        outbox_session_factory,
        topic="phone.disable",
        aggregate_id=user_id,
        payload={"user_id": str(user_id)},
    )
    provider = _CapturingRoutingProvider()

    result = await outbox_delivery_job(
        {
            "session_factory": outbox_session_factory,
            "telephony_provider": provider,
        }
    )

    assert result["delivered"] == 1
    assert provider.enabled == []
    assert provider.disabled == [f"pn_{user_id.hex}"]


@pytest.mark.anyio
async def test_routing_retries_and_reapplies_new_desired_state_after_mid_call_change(
    outbox_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    from app.workers.jobs.outbox_delivery import outbox_delivery_job

    user_id = await _seed_routing_state(
        outbox_session_factory,
        subscription_status="canceled",
        phone_active=True,
    )
    event = await _add_event(
        outbox_session_factory,
        topic="phone.disable",
        aggregate_id=user_id,
        payload={"user_id": str(user_id)},
    )
    current_time = event.next_attempt_at + timedelta(seconds=1)

    class ChangingProvider(_CapturingRoutingProvider):
        async def disable_number(self, *, provider_number_id: str) -> str:
            result = await super().disable_number(
                provider_number_id=provider_number_id
            )
            async with outbox_session_factory() as session:
                subscription = await session.scalar(
                    select(Subscription).where(Subscription.user_id == user_id)
                )
                assert subscription is not None
                subscription.status = "active"
                await session.commit()
            return result

    provider = ChangingProvider()
    ctx = {
        "session_factory": outbox_session_factory,
        "telephony_provider": provider,
        "outbox_now": lambda: current_time,
    }

    first = await outbox_delivery_job(ctx)
    assert first["retried"] == 1
    current_time += timedelta(seconds=10)
    second = await outbox_delivery_job(ctx)

    assert second["delivered"] == 1
    assert provider.disabled == [f"pn_{user_id.hex}"]
    assert provider.enabled == [f"pn_{user_id.hex}"]
    async with outbox_session_factory() as session:
        phone = await session.scalar(
            select(PhoneNumber).where(PhoneNumber.user_id == user_id)
        )
        assert phone is not None
        assert phone.provider_connection_name == "app-active"
        assert phone.is_active is True


@pytest.mark.anyio
async def test_provisioning_provider_runs_without_provisioning_row_lock(
    outbox_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    from app.workers.jobs.phone_provisioning import phone_provisioning_job

    async with outbox_session_factory() as session:
        user = User(
            clerk_user_id=f"provision_lock_{uuid4().hex}",
            email=f"provision_lock_{uuid4().hex}@example.com",
            country_code="FR",
        )
        session.add(user)
        await session.flush()
        provisioning = PhoneNumberProvisioning(
            user_id=user.id,
            target_country_code="FR",
            status="queued",
            attempt_count=0,
            can_retry=False,
        )
        session.add(provisioning)
        await session.commit()
        user_id = user.id
        provisioning_id = provisioning.id

    provider_observations: list[tuple[str, int]] = []

    class LockProbingProvider:
        async def provision_number(
            self,
            *,
            country_code: str,
            operation_key: str | None = None,
        ) -> dict:
            async with outbox_session_factory() as probe_session:
                locked = await probe_session.scalar(
                    select(PhoneNumberProvisioning)
                    .where(PhoneNumberProvisioning.id == provisioning_id)
                    .with_for_update(nowait=True)
                )
                assert locked is not None
                provider_observations.append((locked.status, locked.attempt_count))
                await probe_session.rollback()
            return {
                "e164": "+33123456780",
                "provider_number_id": f"pn_lock_{user_id.hex}",
                "provider_connection_name": "app-active",
            }

        async def enable_number(self, *, provider_number_id: str) -> str:
            return "app-active"

        async def disable_number(self, *, provider_number_id: str) -> str:
            return "app-disabled"

    await phone_provisioning_job(
        {
            "session_factory": outbox_session_factory,
            "telephony_provider": LockProbingProvider(),
        },
        {"user_id": str(user_id)},
        operation_key="provision:lock-boundary",
    )

    assert provider_observations == [("running", 1)]
    async with outbox_session_factory() as session:
        stored = await session.get(PhoneNumberProvisioning, provisioning_id)
        assert stored is not None
        assert stored.status == "succeeded"


@pytest.mark.anyio
async def test_default_provision_handler_threads_durable_idempotency_key(
    outbox_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.workers.jobs import outbox_topics
    from app.workers.jobs.outbox_delivery import outbox_delivery_job

    async with outbox_session_factory() as session:
        user = User(
            clerk_user_id=f"provision_{uuid4().hex}",
            email=f"provision_{uuid4().hex}@example.com",
        )
        session.add(user)
        await session.commit()
        user_id = user.id
    event = await _add_event(
        outbox_session_factory,
        topic="phone.provision",
        aggregate_id=user_id,
        idempotency_key="outbox:phone-provision:stable",
        payload={"user_id": str(user_id)},
    )
    calls: list[tuple[dict, str | None]] = []

    async def capture(_ctx, payload, *, operation_key=None):
        calls.append((payload, operation_key))

    monkeypatch.setattr(outbox_topics, "phone_provisioning_job", capture)

    result = await outbox_delivery_job(
        {"session_factory": outbox_session_factory}
    )

    assert result["delivered"] == 1
    assert calls == [
        ({"user_id": str(user_id)}, event.idempotency_key)
    ]


@pytest.mark.anyio
async def test_provider_idempotency_key_closes_side_effect_before_ack_crash_boundary(
    outbox_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    from app.workers.jobs.outbox_delivery import outbox_delivery_job

    event = await _add_event(
        outbox_session_factory,
        idempotency_key="provider-operation-stable-key",
    )
    current_time = event.next_attempt_at + timedelta(seconds=1)
    provider_seen: set[str] = set()
    provider_side_effect_count = 0
    first_delivery = True

    async def idempotent_provider_handler(_ctx: dict, item: OutboxEvent) -> None:
        nonlocal provider_side_effect_count, first_delivery
        if item.idempotency_key not in provider_seen:
            provider_seen.add(item.idempotency_key)
            provider_side_effect_count += 1
        if first_delivery:
            first_delivery = False
            raise ConnectionError("worker crashed after provider accepted operation")

    ctx = {
        "session_factory": outbox_session_factory,
        "outbox_handlers": {"phone.disable": idempotent_provider_handler},
        "outbox_now": lambda: current_time,
    }

    first = await outbox_delivery_job(ctx)
    assert first["retried"] == 1
    current_time += timedelta(seconds=10)
    second = await outbox_delivery_job(ctx)

    assert second["delivered"] == 1
    assert provider_side_effect_count == 1
    async with outbox_session_factory() as session:
        stored = await session.get(OutboxEvent, event.id)
        assert stored is not None
        assert stored.status == "delivered"
