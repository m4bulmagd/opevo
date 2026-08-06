import asyncio
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base
from app.models.call import Call
from app.models.notification import Notification
from app.models.outbox_event import OutboxEvent
from app.models.recording_egress_operation import RecordingEgressOperation
from app.models.usage_ledger import UsageLedger
from app.models.user import User
from app.repositories.call_repository import CallRepository, CallTransitionError
from app.services.call_lifecycle_service import CallLifecycleService
from app.services.call_reconciliation_service import CallReconciliationService
from tests.reconciliation_settings import TEST_RECONCILIATION_SETTINGS
from app.services.livekit_dispatch_lock import livekit_dispatch_lock


@pytest_asyncio.fixture
async def state_session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("Task 10 state-machine tests require TEST_DATABASE_URL")
    if database_url.startswith("postgresql://"):
        database_url = database_url.replace(
            "postgresql://",
            "postgresql+asyncpg://",
            1,
        )
    if not database_url.startswith("postgresql+asyncpg://"):
        pytest.skip("TEST_DATABASE_URL must identify PostgreSQL")

    schema_name = f"task10_state_{uuid4().hex}"
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


async def seed_user(factory, suffix: str) -> User:
    async with factory() as session:
        user = User(
            clerk_user_id=f"task10_{suffix}_{uuid4().hex}",
            email=f"task10_{suffix}_{uuid4().hex}@example.com",
        )
        session.add(user)
        await session.commit()
        return user


@pytest.mark.anyio
async def test_two_postgresql_workers_compete_for_one_graph_transition(
    state_session_factory,
) -> None:
    user = await seed_user(state_session_factory, "graph_transition")
    async with state_session_factory() as session:
        call = Call(user_id=user.id, status="pending")
        session.add(call)
        await session.commit()
        call_id = call.id

    async def connect():
        async with state_session_factory() as session:
            transitioned = await CallRepository(session).transition(
                call_id,
                from_states={"pending"},
                to_state="connected",
            )
            await session.commit()
            return transitioned.status

    async def fail():
        async with state_session_factory() as session:
            transitioned = await CallRepository(session).transition(
                call_id,
                from_states={"pending"},
                to_state="failed",
                failure_code="dispatch_timeout",
            )
            await session.commit()
            return transitioned.status

    outcomes = await asyncio.gather(connect(), fail(), return_exceptions=True)

    assert sum(isinstance(outcome, CallTransitionError) for outcome in outcomes) == 1
    assert len([outcome for outcome in outcomes if isinstance(outcome, str)]) == 1
    async with state_session_factory() as session:
        stored = await session.get(Call, call_id)
    assert stored.status in {"connected", "failed"}
    assert (stored.failure_code is not None) is (stored.status == "failed")


@pytest.mark.anyio
async def test_parallel_reconciliation_claims_stale_ending_once(
    state_session_factory,
) -> None:
    user = await seed_user(state_session_factory, "reconcile")
    now = datetime.now(UTC)
    async with state_session_factory() as session:
        call = Call(
            user_id=user.id,
            status="ending",
            started_at=now - timedelta(seconds=90),
            ended_at=now - timedelta(seconds=61),
            duration_seconds=29,
            recording_egress_id="egress-concurrent",
            state_changed_at=now - timedelta(seconds=61),
        )
        session.add_all(
            [
                call,
                UsageLedger(
                    user_id=user.id,
                    event_type="subscription_activated",
                    source_id=f"in_{uuid4().hex}",
                    minutes_delta=2,
                    balance_after=2,
                ),
            ]
        )
        await session.commit()
        call_id = call.id

    await asyncio.gather(
        CallReconciliationService(
            state_session_factory, settings=TEST_RECONCILIATION_SETTINGS
        ).reconcile(now),
        CallReconciliationService(
            state_session_factory, settings=TEST_RECONCILIATION_SETTINGS
        ).reconcile(now),
    )

    async with state_session_factory() as session:
        stored = await session.get(Call, call_id)
        assert stored.status == "completed"
        assert stored.finalization_attempt_count == 1
        debit_count = await session.scalar(
            select(func.count())
            .select_from(UsageLedger)
            .where(UsageLedger.call_id == call_id)
        )
        notification_count = await session.scalar(
            select(func.count())
            .select_from(Notification)
            .where(Notification.call_id == call_id)
        )
        operation = await session.scalar(
            select(RecordingEgressOperation).where(
                RecordingEgressOperation.call_id == call_id
            )
        )
        assert operation is not None
        intents = list(
            (
                await session.execute(
                    select(OutboxEvent).where(
                        OutboxEvent.aggregate_id.in_((call_id, operation.id))
                    )
                )
            ).scalars()
        )
    assert debit_count == 1
    assert notification_count == 1
    assert sorted(intent.topic for intent in intents) == [
        "recording.reconcile",
        "summary.generate",
    ]
    reconcile = next(
        intent for intent in intents if intent.topic == "recording.reconcile"
    )
    assert reconcile.payload == {"operation_id": str(operation.id)}


@pytest.mark.anyio
async def test_concurrent_phase_b_creates_each_durable_effect_once(
    state_session_factory,
) -> None:
    user = await seed_user(state_session_factory, "phase_b")
    async with state_session_factory() as session:
        call = Call(
            user_id=user.id,
            status="finalizing",
            duration_seconds=1,
            finalization_attempt_count=1,
            recording_egress_id="egress-phase-b",
        )
        session.add_all(
            [
                call,
                UsageLedger(
                    user_id=user.id,
                    event_type="subscription_activated",
                    source_id=f"in_{uuid4().hex}",
                    minutes_delta=1,
                    balance_after=1,
                ),
            ]
        )
        await session.commit()
        call_id = call.id

    async def complete():
        async with state_session_factory() as session:
            return await CallLifecycleService(session).complete_finalization(
                call_id,
                generation=1,
            )

    first, second = await asyncio.gather(complete(), complete())
    assert [first.already_completed, second.already_completed].count(False) == 1
    assert [first.already_completed, second.already_completed].count(True) == 1

    async with state_session_factory() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(UsageLedger)
                .where(UsageLedger.call_id == call_id)
            )
            == 1
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(Notification)
                .where(Notification.call_id == call_id)
            )
            == 1
        )
        topics = list(
            (
                await session.execute(
                    select(OutboxEvent.topic).where(
                        (OutboxEvent.aggregate_id == call_id)
                        | (
                            OutboxEvent.idempotency_key
                            == f"phone.disable:call:{call_id}"
                        )
                    )
                )
            ).scalars()
        )
    assert sorted(topics) == ["phone.disable", "summary.generate"]


@pytest.mark.anyio
async def test_pending_timeout_waits_for_shared_dispatch_advisory_lock(
    state_session_factory,
) -> None:
    user = await seed_user(state_session_factory, "dispatch_lock")
    now = datetime.now(UTC)
    async with state_session_factory() as session:
        call = Call(
            user_id=user.id,
            status="pending",
            state_changed_at=now - timedelta(seconds=121),
        )
        session.add(call)
        await session.commit()
        call_id = call.id

    async with livekit_dispatch_lock(state_session_factory, call_id):
        task = asyncio.create_task(
            CallReconciliationService(
                state_session_factory, settings=TEST_RECONCILIATION_SETTINGS
            ).reconcile(now)
        )
        await asyncio.sleep(0.1)
        assert task.done() is False

    result = await task
    assert result.failed == 1
    async with state_session_factory() as session:
        stored = await session.get(Call, call_id)
        assert stored.status == "failed"
        assert stored.failure_code == "dispatch_timeout"


@pytest.mark.anyio
async def test_reconciliation_generation_lease_rejects_old_worker_on_postgresql(
    state_session_factory,
    monkeypatch,
) -> None:
    user = await seed_user(state_session_factory, "generation_lease")
    now = datetime.now(UTC)
    async with state_session_factory() as session:
        call = Call(
            user_id=user.id,
            status="finalizing",
            duration_seconds=1,
            finalization_attempt_count=1,
            state_changed_at=now - timedelta(seconds=301),
        )
        session.add_all(
            [
                call,
                UsageLedger(
                    user_id=user.id,
                    event_type="subscription_activated",
                    source_id=f"in_{uuid4().hex}",
                    minutes_delta=1,
                    balance_after=1,
                ),
            ]
        )
        await session.commit()
        call_id = call.id

    original_complete = CallLifecycleService.complete_finalization

    async def defer_phase_b(self, claimed_call_id, *, generation):
        assert claimed_call_id == call_id
        assert generation == 2
        raise RuntimeError("simulated worker interruption")

    monkeypatch.setattr(
        CallLifecycleService,
        "complete_finalization",
        defer_phase_b,
    )
    result = await CallReconciliationService(
        state_session_factory, settings=TEST_RECONCILIATION_SETTINGS
    ).reconcile(now)
    monkeypatch.setattr(
        CallLifecycleService,
        "complete_finalization",
        original_complete,
    )

    assert result.deferred == 1
    async with state_session_factory() as session:
        leased = await session.get(Call, call_id)
        assert leased.status == "finalizing"
        assert leased.finalization_attempt_count == 2
        assert leased.last_reconciled_at == now

    async with state_session_factory() as session:
        stale = await CallLifecycleService(session).complete_finalization(
            call_id,
            generation=1,
        )
        fresh = await CallLifecycleService(session).complete_finalization(
            call_id,
            generation=2,
        )
    assert stale.stale_generation is True
    assert fresh.stale_generation is False

    async with state_session_factory() as session:
        stored = await session.get(Call, call_id)
        assert stored.status == "completed"
        assert (
            await session.scalar(
                select(func.count())
                .select_from(UsageLedger)
                .where(UsageLedger.call_id == call_id)
            )
            == 1
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(Notification)
                .where(Notification.call_id == call_id)
            )
            == 1
        )
