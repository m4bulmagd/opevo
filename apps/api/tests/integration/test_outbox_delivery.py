import asyncio
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
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
from app.models.activation_event import ActivationEvent
from app.models.business_profile import BusinessProfile
from app.models.customer_activation import CustomerActivation
from app.models.outbox_event import OutboxEvent
from app.models.agent_config import AgentConfig
from app.models.phone_number import PhoneNumber
from app.models.phone_number_provisioning import PhoneNumberProvisioning
from app.models.subscription import Subscription
from app.models.user import User
from app.models.usage_ledger import UsageLedger
from app.repositories.account_deactivation_repository import (
    AccountDeactivationObservabilitySnapshot,
    AccountDeactivationRepository,
)
from app.repositories.outbox_repository import OutboxRepository, OutboxSnapshot
from app.repositories.phone_number_provisioning_repository import (
    PhoneNumberProvisioningRepository,
)
from app.repositories.phone_number_repository import PhoneNumberRepository
from app.repositories.recording_egress_operation_repository import (
    RecordingEgressOperationRepository,
    RecordingOperationObservabilitySnapshot,
)
from app.services.outbox_service import OutboxService
from app.services.activation_provisioning_service import ActivationProvisioningService
from app.services.activation_go_live_service import ActivationGoLiveService
from app.services.onboarding_service import OnboardingRetryNotAllowedError, OnboardingService
from app.services.routing_fingerprint import routing_fingerprint


PROVISIONING_NOW = datetime(2026, 7, 18, 9, 0, tzinfo=UTC)


@pytest.fixture
def activation_flow_disabled(monkeypatch: pytest.MonkeyPatch):
    from app.core.config import get_settings

    monkeypatch.setenv("ACTIVATION_FLOW_ENABLED", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _valid_business_hours() -> dict[str, dict[str, object]]:
    return {
        day: {"closed": True, "intervals": []}
        for day in (
            "monday",
            "tuesday",
            "wednesday",
            "thursday",
            "friday",
            "saturday",
            "sunday",
        )
    }


def _add_retryable_activation(
    session: AsyncSession,
    user: User,
    *,
    attempt_count: int,
) -> str:
    activation_id = uuid4()
    operation_key = f"activation:phone.provision:{activation_id}"
    user.country_code = "FR"
    session.add_all(
        [
            BusinessProfile(
                user_id=user.id,
                owner_name="Camille Martin",
                business_name="Atelier Martin",
                business_type="Plomberie",
                public_description="Dépannage.",
                timezone="Europe/Paris",
                business_hours=_valid_business_hours(),
                existing_phone_e164="+33612345678",
                confirmed_carrier="orange",
                receptionist_name="Léa",
            ),
            CustomerActivation(
                id=activation_id,
                user_id=user.id,
                profile_confirmed_revision=1,
                profile_confirmed_at=datetime.now(UTC),
                provisioning_consented_at=datetime.now(UTC),
                provisioning_idempotency_key=operation_key,
            ),
            Subscription(
                user_id=user.id,
                stripe_customer_id=f"cus_{uuid4().hex}",
                stripe_subscription_id=f"sub_{uuid4().hex}",
                plan_tier="starter",
                status="active",
                allocated_minutes=60,
                current_period_start=datetime(2026, 1, 1, tzinfo=UTC),
                current_period_end=datetime(2099, 1, 1, tzinfo=UTC),
            ),
            UsageLedger(
                user_id=user.id,
                event_type="subscription_activated",
                source_id=f"in_{uuid4().hex}",
                minutes_delta=60,
                balance_after=60,
            ),
            PhoneNumberProvisioning(
                user_id=user.id,
                target_country_code="FR",
                status="failed",
                attempt_count=attempt_count,
                can_retry=True,
                provider_operation_key=operation_key,
            ),
        ]
    )
    return operation_key


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
        _add_retryable_activation(session, user, attempt_count=3)
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
async def test_concurrent_go_live_commands_create_one_durable_attempt(
    outbox_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)
    async with outbox_session_factory() as session:
        user = User(
            clerk_user_id=f"go_live_{uuid4().hex}",
            email=f"go_live_{uuid4().hex}@example.com",
            country_code="FR",
        )
        session.add(user)
        await session.flush()
        profile = BusinessProfile(
            user_id=user.id,
            owner_name="Camille Martin",
            business_name="Atelier Martin",
            business_type="Plomberie",
            public_description="Dépannage.",
            timezone="Europe/Paris",
            business_hours=_valid_business_hours(),
            existing_phone_e164="+33199000400",
            confirmed_carrier="orange",
            receptionist_name="Léa",
            content_revision=2,
            routing_revision=2,
        )
        activation = CustomerActivation(
            user_id=user.id,
            profile_confirmed_revision=profile.content_revision,
            profile_confirmed_at=now - timedelta(hours=2),
            provisioning_consented_at=now - timedelta(hours=1),
            verification_status="succeeded",
            forwarding_verified_at=now - timedelta(minutes=30),
        )
        phone = PhoneNumber(
            user_id=user.id,
            e164="+33999000400",
            country_code="FR",
            provider="fake",
            provider_number_id="fake-number-concurrent-go-live",
            provider_connection_name="app-disabled",
            is_active=False,
        )
        config = AgentConfig(
            user_id=user.id,
            agent_name="Léa",
            business_display_name="Atelier Martin",
            owner_context="Camille Martin at Atelier Martin",
            system_prompt="Answer missed calls professionally.",
            knowledge_base="Open weekdays.",
            is_enabled=False,
            profile_projection_revision=profile.content_revision,
        )
        session.add_all(
            [
                profile,
                activation,
                phone,
                config,
                Subscription(
                    user_id=user.id,
                    stripe_customer_id=f"cus_{uuid4().hex}",
                    stripe_subscription_id=f"sub_{uuid4().hex}",
                    plan_tier="starter",
                    status="active",
                    allocated_minutes=60,
                    current_period_start=now - timedelta(days=1),
                    current_period_end=now + timedelta(days=29),
                ),
                UsageLedger(
                    user_id=user.id,
                    event_type="subscription_activated",
                    source_id=f"in_{uuid4().hex}",
                    minutes_delta=60,
                    balance_after=60,
                ),
            ]
        )
        await session.flush()
        activation.verified_routing_fingerprint = routing_fingerprint(profile, phone)
        session.add(
            PhoneNumberProvisioning(
                user_id=user.id,
                phone_number_id=phone.id,
                target_country_code="FR",
                status="succeeded",
                attempt_count=1,
                can_retry=False,
                provider_operation_key=f"activation:phone.provision:{activation.id}",
            )
        )
        await session.commit()
        user_id = user.id

    async def go_live():
        async with outbox_session_factory() as session:
            return await ActivationGoLiveService(
                session,
                now_provider=lambda: now,
            ).go_live(user_id, arq_pool=None)

    first, second = await asyncio.gather(go_live(), go_live())

    assert first.stage == second.stage == "activating"
    async with outbox_session_factory() as session:
        assert await session.scalar(
            select(func.count()).select_from(OutboxEvent)
        ) == 1
        assert await session.scalar(
            select(func.count())
            .select_from(ActivationEvent)
            .where(ActivationEvent.event_type == "go_live_requested")
        ) == 1


@pytest.mark.anyio
async def test_concurrent_provisioning_confirmation_creates_one_durable_intent(
    outbox_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    activation_id = uuid4()
    async with outbox_session_factory() as session:
        user = User(
            clerk_user_id=f"confirm_{uuid4().hex}",
            email=f"confirm_{uuid4().hex}@example.com",
            country_code="FR",
        )
        session.add(user)
        await session.flush()
        session.add_all(
            [
                BusinessProfile(
                    user_id=user.id,
                    owner_name="Camille Martin",
                    business_name="Atelier Martin",
                    business_type="Plomberie",
                    public_description="Dépannage.",
                    timezone="Europe/Paris",
                    business_hours=_valid_business_hours(),
                    existing_phone_e164="+33612345678",
                    confirmed_carrier="orange",
                    receptionist_name="Léa",
                ),
                CustomerActivation(
                    id=activation_id,
                    user_id=user.id,
                    profile_confirmed_revision=1,
                    profile_confirmed_at=PROVISIONING_NOW - timedelta(hours=1),
                ),
                Subscription(
                    user_id=user.id,
                    stripe_customer_id=f"cus_{uuid4().hex}",
                    stripe_subscription_id=f"sub_{uuid4().hex}",
                    plan_tier="starter",
                    status="active",
                    allocated_minutes=60,
                    current_period_start=PROVISIONING_NOW - timedelta(days=30),
                    current_period_end=PROVISIONING_NOW + timedelta(days=30),
                ),
                UsageLedger(
                    user_id=user.id,
                    event_type="subscription_activated",
                    source_id=f"in_{uuid4().hex}",
                    minutes_delta=60,
                    balance_after=60,
                ),
            ]
        )
        await session.commit()
        user_id = user.id

    async def confirm() -> datetime | None:
        async with outbox_session_factory() as session:
            snapshot = await ActivationProvisioningService(
                session,
                now=lambda: PROVISIONING_NOW,
            ).confirm(
                user_id,
                arq_pool=None,
            )
            return snapshot.activation.provisioning_consented_at

    consent_times = await asyncio.gather(confirm(), confirm())

    assert consent_times[0] is not None
    assert consent_times[0] == consent_times[1]
    async with outbox_session_factory() as session:
        activation = await session.scalar(
            select(CustomerActivation).where(CustomerActivation.user_id == user_id)
        )
        provisioning = await session.scalar(
            select(PhoneNumberProvisioning).where(
                PhoneNumberProvisioning.user_id == user_id
            )
        )
        assert activation is not None
        assert provisioning is not None
        operation_key = f"activation:provision:{activation_id}:g1"
        assert activation.provisioning_idempotency_key == operation_key
        assert provisioning.provider_operation_key == operation_key
        assert await session.scalar(
            select(func.count())
            .select_from(OutboxEvent)
            .where(OutboxEvent.topic == "phone.provision")
        ) == 1
        assert await session.scalar(
            select(func.count())
            .select_from(ActivationEvent)
            .where(ActivationEvent.event_type == "provisioning_consented")
        ) == 1


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
        async with outbox_session_factory() as committed_session:
            committed = await committed_session.get(OutboxEvent, event.id)
            assert committed is not None
            assert committed.status == "failed"
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
async def test_non_exhausting_delivery_error_keeps_using_bounded_backoff(
    outbox_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    from app.workers.jobs.outbox_delivery import (
        OUTBOX_RETRY_DELAYS,
        OutboxDeliveryError,
        outbox_delivery_job,
    )

    event = await _add_event(outbox_session_factory)
    current_time = event.next_attempt_at + timedelta(seconds=1)

    async def draining_handler(_ctx: dict, _event: OutboxEvent) -> None:
        raise OutboxDeliveryError(
            "account_call_draining",
            retryable=True,
            exhaustible=False,
        )

    ctx = {
        "session_factory": outbox_session_factory,
        "outbox_handlers": {"phone.disable": draining_handler},
        "outbox_now": lambda: current_time,
    }
    for expected_attempt in range(1, len(OUTBOX_RETRY_DELAYS) + 4):
        result = await outbox_delivery_job(ctx)
        assert result == {
            "claimed": 1,
            "delivered": 0,
            "retried": 1,
            "failed": 0,
        }
        async with outbox_session_factory() as session:
            stored = await session.get(OutboxEvent, event.id)
            assert stored is not None
            assert stored.status == "pending"
            assert stored.attempt_count == expected_attempt
            assert stored.last_error_code == "account_call_draining"
            current_time = stored.next_attempt_at


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

    await _add_event(outbox_session_factory)

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
async def test_delivery_claims_each_event_only_when_its_handler_is_ready(
    outbox_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    from app.workers.jobs.outbox_delivery import outbox_delivery_job

    first = await _add_event(outbox_session_factory)
    second = await _add_event(outbox_session_factory)
    handler_order: list[UUID] = []

    async def handler(_ctx: dict, item: OutboxEvent) -> None:
        if item.id == first.id:
            async with outbox_session_factory() as probe_session:
                later = await probe_session.get(OutboxEvent, second.id)
                assert later is not None
                assert later.status == "pending"
        handler_order.append(item.id)

    result = await outbox_delivery_job(
        {
            "session_factory": outbox_session_factory,
            "outbox_handlers": {"phone.disable": handler},
        }
    )

    assert result == {"claimed": 2, "delivered": 2, "retried": 0, "failed": 0}
    assert handler_order == [first.id, second.id]


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


@pytest.mark.parametrize(
    ("failure_target", "failed_operation"),
    [
        ("outbox_query", "collect_outbox_snapshot"),
        ("outbox_metric", "collect_outbox_snapshot"),
        ("recording_query", "collect_recording_operation_snapshot"),
        ("recording_metric", "collect_recording_operation_snapshot"),
        ("deactivation_query", "collect_account_deactivation_snapshot"),
        ("deactivation_metric", "collect_account_deactivation_snapshot"),
    ],
)
@pytest.mark.anyio
async def test_reconciliation_snapshot_failures_are_independently_isolated(
    outbox_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    failure_target: str,
    failed_operation: str,
) -> None:
    from app.workers.jobs.outbox_delivery import outbox_reconciliation_job

    event = await _add_event(outbox_session_factory)
    delivered: list[str] = []
    query_attempts: list[str] = []
    snapshot_sessions: list[AsyncSession] = []
    metric_attempts: list[str] = []
    recording_clock = datetime(2026, 7, 19, 12, 30, tzinfo=UTC)
    recording_clocks: list[datetime] = []
    deactivation_clock = datetime(2026, 7, 19, 12, 31, tzinfo=UTC)
    deactivation_clocks: list[datetime] = []
    private_exception_message = "customer-room credential=do-not-log"

    outbox_snapshot = OutboxSnapshot(
        counts={
            "pending": 0,
            "processing": 0,
            "delivered": 1,
            "failed": 0,
        },
        oldest_unfinished_age_seconds=0.0,
    )
    recording_snapshot = RecordingOperationObservabilitySnapshot(
        counts={
            "prepared": 0,
            "starting": 0,
            "started": 0,
            "not_started": 0,
            "uncertain": 0,
        },
        oldest_unresolved_age_seconds=0.0,
        pending_stop_count=0,
        oldest_pending_stop_age_seconds=0.0,
        pending_deletion_count=0,
        oldest_pending_deletion_age_seconds=0.0,
    )
    deactivation_snapshot = AccountDeactivationObservabilitySnapshot(
        counts={},
        oldest_incomplete_age_seconds=0.0,
        attention_counts={
            "owner_request": 0,
            "subscription_ended": 0,
        },
    )

    async def collect_outbox_snapshot(
        repository: OutboxRepository,
        _now: datetime,
    ) -> OutboxSnapshot:
        query_attempts.append("outbox")
        snapshot_sessions.append(repository.session)
        if failure_target == "outbox_query":
            raise RuntimeError(private_exception_message)
        return outbox_snapshot

    async def collect_recording_snapshot(
        repository: RecordingEgressOperationRepository,
        now: datetime,
    ) -> RecordingOperationObservabilitySnapshot:
        query_attempts.append("recording")
        snapshot_sessions.append(repository.session)
        recording_clocks.append(now)
        if failure_target == "recording_query":
            raise RuntimeError(private_exception_message)
        return recording_snapshot

    async def collect_deactivation_snapshot(
        repository: AccountDeactivationRepository,
        now: datetime,
    ) -> AccountDeactivationObservabilitySnapshot:
        query_attempts.append("deactivation")
        snapshot_sessions.append(repository.session)
        deactivation_clocks.append(now)
        if failure_target == "deactivation_query":
            raise RuntimeError(private_exception_message)
        return deactivation_snapshot

    class SnapshotObservability:
        def record_outbox_snapshot(self, snapshot: OutboxSnapshot) -> None:
            assert snapshot is outbox_snapshot
            metric_attempts.append("outbox")
            if failure_target == "outbox_metric":
                raise RuntimeError(private_exception_message)

        def record_recording_operation_snapshot(
            self,
            snapshot: RecordingOperationObservabilitySnapshot,
        ) -> None:
            assert snapshot is recording_snapshot
            metric_attempts.append("recording")
            if failure_target == "recording_metric":
                raise RuntimeError(private_exception_message)

        def record_account_deactivation_snapshot(
            self,
            snapshot: AccountDeactivationObservabilitySnapshot,
        ) -> None:
            assert snapshot is deactivation_snapshot
            metric_attempts.append("deactivation")
            if failure_target == "deactivation_metric":
                raise RuntimeError(private_exception_message)

    async def handler(_ctx: dict, item: OutboxEvent) -> None:
        delivered.append(item.idempotency_key)

    monkeypatch.setattr(
        OutboxRepository,
        "observability_snapshot",
        collect_outbox_snapshot,
    )
    monkeypatch.setattr(
        RecordingEgressOperationRepository,
        "observability_snapshot",
        collect_recording_snapshot,
    )
    monkeypatch.setattr(
        AccountDeactivationRepository,
        "observability_snapshot",
        collect_deactivation_snapshot,
    )
    caplog.set_level(
        logging.WARNING,
        logger="app.workers.jobs.outbox_delivery",
    )

    result = await outbox_reconciliation_job(
        {
            "session_factory": outbox_session_factory,
            "outbox_handlers": {"phone.disable": handler},
            "observability": SnapshotObservability(),
            "recording_observability_now": lambda: recording_clock,
            "account_deactivation_observability_now": lambda: deactivation_clock,
        }
    )

    assert result == {"claimed": 1, "delivered": 1, "retried": 0, "failed": 0}
    assert delivered == [event.idempotency_key]
    assert query_attempts == ["outbox", "recording", "deactivation"]
    assert len(snapshot_sessions) == 3
    assert len({id(session) for session in snapshot_sessions}) == 3
    assert recording_clocks == [recording_clock]
    assert deactivation_clocks == [deactivation_clock]

    expected_metric_attempts = ["outbox", "recording", "deactivation"]
    if failure_target == "outbox_query":
        expected_metric_attempts.remove("outbox")
    if failure_target == "recording_query":
        expected_metric_attempts.remove("recording")
    if failure_target == "deactivation_query":
        expected_metric_attempts.remove("deactivation")
    assert metric_attempts == expected_metric_attempts

    warning_records = [
        record
        for record in caplog.records
        if record.name == "app.workers.jobs.outbox_delivery"
        and record.levelno == logging.WARNING
    ]
    assert len(warning_records) == 1
    assert warning_records[0].getMessage() == (
        "event=observability_snapshot_failed "
        f"operation={failed_operation} "
        "error_type=RuntimeError status=failed"
    )
    assert private_exception_message not in caplog.text


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
    period_start: datetime = datetime(2026, 1, 1, tzinfo=UTC),
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
                current_period_start=period_start,
                current_period_end=datetime(2099, 1, 1, tzinfo=UTC),
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
        payload={"user_id": str(user_id), "lifecycle_generation": 1},
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
    activation_flow_disabled,
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
    activation_flow_disabled,
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

    class TrackingFactory:
        def __init__(self) -> None:
            self.sessions: list[AsyncSession] = []

        @asynccontextmanager
        async def __call__(self):
            async with outbox_session_factory() as session:
                self.sessions.append(session)
                yield session

        def assert_transaction_free(self) -> None:
            assert all(not session.in_transaction() for session in self.sessions)

    tracking_factory = TrackingFactory()
    provider_observations: list[tuple[str, int]] = []

    class LockProbingProvider:
        async def provision_number(
            self,
            *,
            country_code: str,
            operation_key: str | None = None,
        ) -> dict:
            tracking_factory.assert_transaction_free()
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
            "session_factory": tracking_factory,
            "telephony_provider": LockProbingProvider(),
        },
        {"user_id": str(user_id), "lifecycle_generation": 1},
        provider_operation_key="provision:lock-boundary",
    )

    assert provider_observations == [("running", 1)]
    async with outbox_session_factory() as session:
        stored = await session.get(PhoneNumberProvisioning, provisioning_id)
        assert stored is not None
        assert stored.status == "succeeded"


@pytest.mark.anyio
async def test_same_provisioning_key_serializes_provider_execution_past_lease_overlap(
    outbox_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    from app.workers.jobs.phone_provisioning import phone_provisioning_job

    async with outbox_session_factory() as session:
        user = User(
            clerk_user_id=f"provision_serial_{uuid4().hex}",
            email=f"provision_serial_{uuid4().hex}@example.com",
            country_code="FR",
        )
        session.add(user)
        await session.commit()
        user_id = user.id

    first_provider_started = asyncio.Event()
    allow_first_provider_to_finish = asyncio.Event()
    second_provider_started = asyncio.Event()
    provider_calls: list[str | None] = []

    class BlockingProvider:
        async def provision_number(
            self,
            *,
            country_code: str,
            operation_key: str | None = None,
        ) -> dict:
            provider_calls.append(operation_key)
            if len(provider_calls) == 1:
                first_provider_started.set()
                await allow_first_provider_to_finish.wait()
            else:
                second_provider_started.set()
            return {
                "e164": "+33123456781",
                "provider_number_id": f"pn_serial_{user_id.hex}",
                "provider_connection_name": "app-disabled",
            }

        async def enable_number(self, *, provider_number_id: str) -> str:
            return "app-active"

        async def disable_number(self, *, provider_number_id: str) -> str:
            return "app-disabled"

    ctx = {
        "session_factory": outbox_session_factory,
        "telephony_provider": BlockingProvider(),
    }
    payload = {"user_id": str(user_id), "lifecycle_generation": 1}
    operation_key = "outbox:phone-provision:lease-overlap"
    first = asyncio.create_task(
        phone_provisioning_job(
            ctx,
            payload,
            provider_operation_key=operation_key,
        )
    )
    await asyncio.wait_for(first_provider_started.wait(), timeout=2)
    reclaimed = asyncio.create_task(
        phone_provisioning_job(
            ctx,
            payload,
            provider_operation_key=operation_key,
        )
    )
    await asyncio.sleep(0.1)
    overlapped_provider_execution = second_provider_started.is_set()
    allow_first_provider_to_finish.set()
    results = await asyncio.gather(first, reclaimed, return_exceptions=True)

    assert not overlapped_provider_execution
    assert results == [None, None]
    assert provider_calls == [operation_key]


@pytest.mark.anyio
async def test_default_provision_handler_threads_key_but_requires_durable_number(
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
        await session.flush()
        user_id = user.id
        session.add(
            PhoneNumberProvisioning(
                user_id=user_id,
                target_country_code="FR",
                status="queued",
                attempt_count=0,
                can_retry=False,
                provider_operation_key=(
                    "activation:phone.provision:stable-provider-operation"
                ),
            )
        )
        await session.commit()
    event = await _add_event(
        outbox_session_factory,
        topic="phone.provision",
        aggregate_id=user_id,
        idempotency_key="outbox:phone-provision:stable",
        payload={"user_id": str(user_id), "lifecycle_generation": 1},
    )
    calls: list[tuple[dict, str | None]] = []

    async def capture(_ctx, payload, *, provider_operation_key):
        calls.append((payload, provider_operation_key))

    monkeypatch.setattr(outbox_topics, "phone_provisioning_job", capture)

    result = await outbox_delivery_job(
        {"session_factory": outbox_session_factory}
    )

    assert result == {"claimed": 1, "delivered": 0, "retried": 0, "failed": 1}
    assert calls == [
        (
            {"user_id": str(user_id), "lifecycle_generation": 1},
            "activation:phone.provision:stable-provider-operation",
        )
    ]
    async with outbox_session_factory() as session:
        stored = await session.get(OutboxEvent, event.id)
        assert stored is not None
        assert stored.status == "failed"
        assert stored.last_error_code == "provider_terminal"


@pytest.mark.anyio
async def test_provisioning_crash_replays_same_key_and_stays_disabled_until_routing(
    outbox_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    from app.workers.jobs.outbox_delivery import outbox_delivery_job

    operation_key = "outbox:phone-provision:crash-safe-disabled"
    async with outbox_session_factory() as session:
        user = User(
            clerk_user_id=f"provision_crash_{uuid4().hex}",
            email=f"provision_crash_{uuid4().hex}@example.com",
            country_code="FR",
        )
        session.add(user)
        await session.flush()
        session.add(
            PhoneNumberProvisioning(
                user_id=user.id,
                target_country_code="FR",
                status="queued",
                attempt_count=0,
                can_retry=False,
                provider_operation_key=operation_key,
            )
        )
        await session.commit()
        user_id = user.id
    event = await _add_event(
        outbox_session_factory,
        topic="phone.provision",
        aggregate_id=user_id,
        idempotency_key=operation_key,
        payload={"user_id": str(user_id), "lifecycle_generation": 1},
    )
    current_time = event.next_attempt_at + timedelta(seconds=1)

    class CrashThenReconcileDisabledProvider:
        def __init__(self) -> None:
            self.operation_keys: list[str | None] = []
            self.provisioned_connections: list[str] = []
            self.routing_disables: list[str] = []

        async def provision_number(
            self,
            *,
            country_code: str,
            operation_key: str | None = None,
        ) -> dict:
            self.operation_keys.append(operation_key)
            self.provisioned_connections.append("app-disabled")
            if len(self.operation_keys) == 1:
                raise RuntimeError("simulated crash after provider accepted order")
            return {
                "e164": "+33123456782",
                "provider_number_id": f"pn_crash_{user_id.hex}",
                "provider_connection_name": "app-disabled",
            }

        async def enable_number(self, *, provider_number_id: str) -> str:
            raise AssertionError("an ineligible new number must not be enabled")

        async def disable_number(self, *, provider_number_id: str) -> str:
            self.routing_disables.append(provider_number_id)
            return "app-disabled"

    provider = CrashThenReconcileDisabledProvider()
    ctx = {
        "session_factory": outbox_session_factory,
        "telephony_provider": provider,
        "outbox_now": lambda: current_time,
    }

    first = await outbox_delivery_job(ctx)
    assert first == {"claimed": 1, "delivered": 0, "retried": 1, "failed": 0}
    async with outbox_session_factory() as session:
        assert await PhoneNumberRepository(session).get_by_user_id(user_id) is None
        provisioning = await PhoneNumberProvisioningRepository(
            session
        ).get_by_user_id(user_id)
        assert provisioning is not None
        assert provisioning.can_retry is True
        assert provisioning.provider_operation_key == event.idempotency_key

    current_time += timedelta(seconds=10)
    second = await outbox_delivery_job(ctx)

    assert second == {"claimed": 1, "delivered": 1, "retried": 0, "failed": 0}
    assert provider.operation_keys == [event.idempotency_key, event.idempotency_key]
    assert provider.provisioned_connections == ["app-disabled", "app-disabled"]
    assert provider.routing_disables == [f"pn_crash_{user_id.hex}"]
    async with outbox_session_factory() as session:
        phone_number = await PhoneNumberRepository(session).get_by_user_id(user_id)
        assert phone_number is not None
        assert phone_number.provider_connection_name == "app-disabled"
        assert phone_number.is_active is False


@pytest.mark.anyio
async def test_pending_order_retries_outbox_without_customer_retry(
    outbox_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    from app.providers.telephony.base import TelephonyProvisioningPending
    from app.workers.jobs.outbox_delivery import outbox_delivery_job

    operation_key = "outbox:phone-provision:pending-order"
    async with outbox_session_factory() as session:
        user = User(
            clerk_user_id=f"provision_pending_{uuid4().hex}",
            email=f"provision_pending_{uuid4().hex}@example.com",
            country_code="FR",
        )
        session.add(user)
        await session.flush()
        session.add(
            PhoneNumberProvisioning(
                user_id=user.id,
                target_country_code="FR",
                status="queued",
                attempt_count=0,
                can_retry=False,
                provider_operation_key=operation_key,
            )
        )
        await session.commit()
        user_id = user.id
    event = await _add_event(
        outbox_session_factory,
        topic="phone.provision",
        aggregate_id=user_id,
        idempotency_key=operation_key,
        payload={"user_id": str(user_id), "lifecycle_generation": 1},
    )

    class PendingProvider:
        async def provision_number(self, **_kwargs) -> dict:
            raise TelephonyProvisioningPending(reason="existing_order_pending")

        async def enable_number(self, *, provider_number_id: str) -> str:
            raise AssertionError

        async def disable_number(self, *, provider_number_id: str) -> str:
            raise AssertionError

    result = await outbox_delivery_job(
        {
            "session_factory": outbox_session_factory,
            "telephony_provider": PendingProvider(),
        }
    )

    assert result == {"claimed": 1, "delivered": 0, "retried": 1, "failed": 0}
    async with outbox_session_factory() as session:
        stored_event = await session.get(OutboxEvent, event.id)
        provisioning = await PhoneNumberProvisioningRepository(
            session
        ).get_by_user_id(user_id)
        assert stored_event is not None
        assert stored_event.status == "pending"
        assert stored_event.last_error_code == "provider_retryable"
        assert provisioning is not None
        assert provisioning.status == "running"
        assert provisioning.can_retry is False
        assert provisioning.last_error_reason == "existing_order_pending"
        assert provisioning.provider_operation_key == event.idempotency_key


@pytest.mark.anyio
async def test_existing_order_conflict_is_terminal_and_disables_customer_retry(
    outbox_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    from app.providers.telephony.base import TelephonyProvisioningReviewRequired
    from app.workers.jobs.outbox_delivery import outbox_delivery_job

    operation_key = "outbox:phone-provision:order-conflict"
    async with outbox_session_factory() as session:
        user = User(
            clerk_user_id=f"provision_conflict_{uuid4().hex}",
            email=f"provision_conflict_{uuid4().hex}@example.com",
            country_code="FR",
        )
        session.add(user)
        await session.flush()
        session.add(
            PhoneNumberProvisioning(
                user_id=user.id,
                target_country_code="FR",
                status="queued",
                attempt_count=0,
                can_retry=False,
                provider_operation_key=operation_key,
            )
        )
        await session.commit()
        user_id = user.id
    event = await _add_event(
        outbox_session_factory,
        topic="phone.provision",
        aggregate_id=user_id,
        idempotency_key=operation_key,
        payload={"user_id": str(user_id), "lifecycle_generation": 1},
    )

    class ConflictProvider:
        async def provision_number(self, **_kwargs) -> dict:
            raise TelephonyProvisioningReviewRequired(
                reason="existing_order_conflict",
                payload={
                    "event": "phone_number_provisioning_review_required",
                    "contact_support": True,
                    "manual_review_required": True,
                },
            )

        async def enable_number(self, *, provider_number_id: str) -> str:
            raise AssertionError

        async def disable_number(self, *, provider_number_id: str) -> str:
            raise AssertionError

    result = await outbox_delivery_job(
        {
            "session_factory": outbox_session_factory,
            "telephony_provider": ConflictProvider(),
        }
    )

    assert result == {"claimed": 1, "delivered": 0, "retried": 0, "failed": 1}
    async with outbox_session_factory() as session:
        stored_event = await session.get(OutboxEvent, event.id)
        provisioning = await PhoneNumberProvisioningRepository(
            session
        ).get_by_user_id(user_id)
        assert stored_event is not None
        assert stored_event.status == "failed"
        assert stored_event.last_error_code == "provider_terminal"
        assert provisioning is not None
        assert provisioning.status == "failed"
        assert provisioning.can_retry is False
        assert provisioning.last_error_reason == "existing_order_conflict"


@pytest.mark.anyio
async def test_customer_retry_new_event_reuses_original_provider_operation_key(
    outbox_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    from app.workers.jobs.outbox_delivery import outbox_delivery_job

    async with outbox_session_factory() as session:
        user = User(
            clerk_user_id=f"provision_customer_retry_{uuid4().hex}",
            email=f"provision_customer_retry_{uuid4().hex}@example.com",
            country_code="FR",
        )
        session.add(user)
        await session.flush()
        original_provider_key = _add_retryable_activation(
            session,
            user,
            attempt_count=1,
        )
        await session.commit()
        user_id = user.id

    async with outbox_session_factory() as session:
        await OnboardingService(session).retry_provisioning(
            user_id,
            arq_pool=None,
        )
        retry_event = await session.scalar(
            select(OutboxEvent).where(OutboxEvent.aggregate_id == user_id)
        )
        assert retry_event is not None

    provider_keys: list[str | None] = []

    class RetryProvider:
        async def provision_number(
            self,
            *,
            country_code: str,
            operation_key: str | None = None,
        ) -> dict:
            provider_keys.append(operation_key)
            return {
                "e164": "+33123456783",
                "provider_number_id": f"pn_customer_retry_{user_id.hex}",
                "provider_connection_name": "app-disabled",
            }

        async def enable_number(self, *, provider_number_id: str) -> str:
            raise AssertionError

        async def disable_number(self, *, provider_number_id: str) -> str:
            return "app-disabled"

    result = await outbox_delivery_job(
        {
            "session_factory": outbox_session_factory,
            "telephony_provider": RetryProvider(),
        }
    )

    assert result == {"claimed": 1, "delivered": 1, "retried": 0, "failed": 0}
    assert retry_event.idempotency_key != original_provider_key
    assert provider_keys == [original_provider_key]
    async with outbox_session_factory() as session:
        provisioning = await PhoneNumberProvisioningRepository(
            session
        ).get_by_user_id(user_id)
        assert provisioning is not None
        assert provisioning.status == "succeeded"
        assert provisioning.provider_operation_key == original_provider_key


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
