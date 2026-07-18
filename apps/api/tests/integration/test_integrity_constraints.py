import asyncio
from datetime import UTC, datetime, timedelta
import os
from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.models import Base
from app.models.activation_event import ActivationEvent
from app.models.business_profile import BusinessProfile
from app.models.call import Call
from app.models.call_message import CallMessage
from app.models.customer_activation import CustomerActivation
from app.models.phone_number import PhoneNumber
from app.models.subscription import Subscription
from app.models.usage_ledger import UsageLedger
from app.models.user import User
from app.models.webhook_event import WebhookEvent
from app.repositories.activation_event_repository import ActivationEventRepository
from app.repositories.business_profile_repository import BusinessProfileRepository
from app.repositories.customer_activation_repository import (
    CustomerActivationRepository,
)
from app.repositories.webhook_event_repository import WebhookEventRepository
from app.services.forwarding_verification_service import (
    ForwardingVerificationService,
    build_expiry_user_claim_statement,
)


@pytest_asyncio.fixture
async def postgres_session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip(
            "PostgreSQL integrity tests require TEST_DATABASE_URL; "
            "the normal application DATABASE_URL is never used"
        )
    if not database_url.startswith(("postgresql+asyncpg://", "postgresql://")):
        pytest.skip("TEST_DATABASE_URL must identify a PostgreSQL database")
    if database_url.startswith("postgresql://"):
        database_url = database_url.replace(
            "postgresql://",
            "postgresql+asyncpg://",
            1,
        )

    schema_name = f"task4_integrity_{uuid4().hex}"
    quoted_schema = f'"{schema_name}"'
    admin_engine = create_async_engine(database_url, isolation_level="AUTOCOMMIT")
    test_engine = None

    try:
        async with admin_engine.connect() as connection:
            await connection.execute(text(f"CREATE SCHEMA {quoted_schema}"))

        test_engine = create_async_engine(
            database_url,
            connect_args={"server_settings": {"search_path": quoted_schema}},
        )
        async with test_engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        yield async_sessionmaker(test_engine, expire_on_commit=False)
    finally:
        if test_engine is not None:
            await test_engine.dispose()
        async with admin_engine.connect() as connection:
            await connection.execute(text(f"DROP SCHEMA IF EXISTS {quoted_schema} CASCADE"))
        await admin_engine.dispose()


async def _create_user(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    suffix: str,
) -> User:
    async with session_factory() as session:
        user = User(
            clerk_user_id=f"user_{suffix}_{uuid4().hex}",
            email=f"{suffix}_{uuid4().hex}@example.com",
        )
        session.add(user)
        await session.commit()
        return user


async def _commit_one(
    session_factory: async_sessionmaker[AsyncSession],
    instance: object,
) -> bool:
    async with session_factory() as session:
        session.add(instance)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            return False
        return True


async def _wait_for_two_lock_waiters(
    observer: AsyncSession,
    *,
    backend_pids: tuple[int, int],
) -> None:
    activity = []
    for _ in range(200):
        activity = list(
            (
                await observer.execute(
                    text(
                        "SELECT application_name, state, wait_event_type, "
                        "wait_event, query FROM pg_stat_activity "
                        "WHERE pid IN (:first_pid, :second_pid)"
                    ),
                    {
                        "first_pid": backend_pids[0],
                        "second_pid": backend_pids[1],
                    },
                )
            ).all()
        )
        waiter_count = sum(row.wait_event_type == "Lock" for row in activity)
        if waiter_count == 2:
            return
        await asyncio.sleep(0.01)
    pytest.fail(
        "Concurrent repository calls did not both reach a database lock: "
        f"activity={activity!r}"
    )


@pytest.mark.anyio
async def test_webhook_repository_race_has_one_durable_provider_event(
    postgres_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    external_event_id = f"evt_{uuid4().hex}"

    async def record() -> bool:
        async with postgres_session_factory() as session:
            inserted = await WebhookEventRepository(session).record_if_new(
                provider="stripe",
                external_event_id=external_event_id,
                event_type="invoice.paid",
                payload={"id": external_event_id},
            )
            await session.commit()
            return inserted

    results = await asyncio.gather(record(), record())

    assert sorted(results) == [False, True]
    async with postgres_session_factory() as session:
        durable_count = await session.scalar(
            select(func.count())
            .select_from(WebhookEvent)
            .where(
                WebhookEvent.provider == "stripe",
                WebhookEvent.external_event_id == external_event_id,
            )
        )
    assert durable_count == 1


@pytest.mark.anyio
async def test_call_debit_race_has_one_durable_call_event(
    postgres_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    user = await _create_user(postgres_session_factory, suffix="call_debit")
    async with postgres_session_factory() as session:
        call = Call(user_id=user.id, status="completed")
        session.add(call)
        await session.commit()

    results = await asyncio.gather(
        _commit_one(
            postgres_session_factory,
            UsageLedger(
                user_id=user.id,
                call_id=call.id,
                event_type="call_completed",
                minutes_delta=-1,
            ),
        ),
        _commit_one(
            postgres_session_factory,
            UsageLedger(
                user_id=user.id,
                call_id=call.id,
                event_type="call_completed",
                minutes_delta=-1,
            ),
        ),
    )

    assert sorted(results) == [False, True]
    async with postgres_session_factory() as session:
        durable_count = await session.scalar(
            select(func.count())
            .select_from(UsageLedger)
            .where(
                UsageLedger.call_id == call.id,
                UsageLedger.event_type == "call_completed",
            )
        )
    assert durable_count == 1


@pytest.mark.anyio
async def test_integrity_constraints_cover_all_identities(
    postgres_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    user = await _create_user(postgres_session_factory, suffix="identities")
    async with postgres_session_factory() as session:
        call = Call(user_id=user.id, status="completed")
        session.add(call)
        await session.commit()

    cases = [
        (
            WebhookEvent(
                provider="stripe",
                external_event_id="evt_identity",
                event_type="invoice.paid",
                payload={},
            ),
            WebhookEvent(
                provider="stripe",
                external_event_id="evt_identity",
                event_type="invoice.updated",
                payload={},
            ),
        ),
        (
            UsageLedger(
                user_id=user.id,
                event_type="invoice_paid_reset",
                source_id="in_identity",
                minutes_delta=60,
            ),
            UsageLedger(
                user_id=user.id,
                event_type="invoice_paid_reset",
                source_id="in_identity",
                minutes_delta=60,
            ),
        ),
        (
            CallMessage(call_id=call.id, speaker="CALLER", text="one", sequence_number=1),
            CallMessage(call_id=call.id, speaker="AGENT", text="two", sequence_number=1),
        ),
        (
            Subscription(
                user_id=user.id,
                plan_tier="starter",
                status="active",
                allocated_minutes=60,
            ),
            Subscription(
                user_id=user.id,
                plan_tier="starter",
                status="active",
                allocated_minutes=60,
            ),
        ),
    ]

    for first, duplicate in cases:
        assert await _commit_one(postgres_session_factory, first) is True
        assert await _commit_one(postgres_session_factory, duplicate) is False

    assert await _commit_one(
        postgres_session_factory,
        WebhookEvent(
            provider="clerk",
            external_event_id="evt_identity",
            event_type="user.created",
            payload={},
        ),
    ) is True
    assert await _commit_one(
        postgres_session_factory,
        UsageLedger(
            user_id=user.id,
            event_type="adjustment",
            source_id=None,
            minutes_delta=1,
        ),
    ) is True


@pytest.mark.anyio
async def test_one_active_call_per_user_but_completed_call_releases_identity(
    postgres_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    user = await _create_user(postgres_session_factory, suffix="active_call")

    assert await _commit_one(
        postgres_session_factory,
        Call(user_id=user.id, status="pending"),
    ) is True
    assert await _commit_one(
        postgres_session_factory,
        Call(user_id=user.id, status="finalizing"),
    ) is False

    async with postgres_session_factory() as session:
        active_call = await session.scalar(
            select(Call).where(Call.user_id == user.id, Call.status == "pending")
        )
        assert active_call is not None
        active_call.status = "completed"
        await session.commit()

    assert await _commit_one(
        postgres_session_factory,
        Call(user_id=user.id, status="connected"),
    ) is True


@pytest.mark.anyio
@pytest.mark.parametrize(
    "instance_factory",
    [
        lambda user_id: Subscription(
            user_id=user_id,
            plan_tier="starter",
            status="active",
            allocated_minutes=-1,
        ),
        lambda user_id: Call(user_id=user_id, status="completed", duration_seconds=-1),
        lambda user_id: Call(user_id=user_id, status="completed", minutes_charged=-1),
    ],
)
async def test_nonnegative_checks_reject_negative_values(
    postgres_session_factory: async_sessionmaker[AsyncSession],
    instance_factory,
) -> None:
    user = await _create_user(postgres_session_factory, suffix="nonnegative")

    assert await _commit_one(postgres_session_factory, instance_factory(user.id)) is False


@pytest.mark.anyio
@pytest.mark.parametrize(
    "instance_factory",
    [
        lambda user_id: PhoneNumber(
            user_id=user_id,
            e164=f"+33{uuid4().int % 10**9:09d}",
            country_code="FR",
        ),
        lambda user_id: BusinessProfile(user_id=user_id),
        lambda user_id: CustomerActivation(user_id=user_id),
    ],
    ids=["phone_number", "business_profile", "customer_activation"],
)
async def test_activation_owner_race_commits_exactly_one_row(
    postgres_session_factory: async_sessionmaker[AsyncSession],
    instance_factory,
) -> None:
    user = await _create_user(postgres_session_factory, suffix="activation_owner")

    results = await asyncio.gather(
        _commit_one(postgres_session_factory, instance_factory(user.id)),
        _commit_one(postgres_session_factory, instance_factory(user.id)),
    )

    assert sorted(results) == [False, True]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("table_name", "repository_type", "model_type"),
    [
        (
            "business_profiles",
            BusinessProfileRepository,
            BusinessProfile,
        ),
        (
            "customer_activations",
            CustomerActivationRepository,
            CustomerActivation,
        ),
    ],
    ids=["business_profile", "customer_activation"],
)
async def test_activation_get_or_create_race_returns_one_durable_row_to_both_callers(
    postgres_session_factory: async_sessionmaker[AsyncSession],
    table_name,
    repository_type,
    model_type,
) -> None:
    user = await _create_user(postgres_session_factory, suffix="activation_create")
    backend_pid_queue: asyncio.Queue[int] = asyncio.Queue()

    async def get_or_create():
        async with postgres_session_factory() as session:
            backend_pid = await session.scalar(select(func.pg_backend_pid()))
            assert backend_pid is not None
            await backend_pid_queue.put(backend_pid)
            record = await repository_type(session).get_or_create_for_update(user.id)
            await session.commit()
            return record.id

    async with postgres_session_factory() as gate_session:
        await gate_session.execute(text(f"LOCK TABLE {table_name} IN SHARE MODE"))
        tasks = [
            asyncio.create_task(get_or_create()),
            asyncio.create_task(get_or_create()),
        ]
        backend_pids = (
            await backend_pid_queue.get(),
            await backend_pid_queue.get(),
        )
        await _wait_for_two_lock_waiters(
            gate_session,
            backend_pids=backend_pids,
        )
        await gate_session.commit()
        record_ids = await asyncio.gather(*tasks)

    assert record_ids[0] == record_ids[1]
    async with postgres_session_factory() as session:
        durable_count = await session.scalar(
            select(func.count())
            .select_from(model_type)
            .where(model_type.user_id == user.id)
        )
    assert durable_count == 1


@pytest.mark.anyio
async def test_activation_event_append_race_returns_first_durable_event_to_both_callers(
    postgres_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    user = await _create_user(postgres_session_factory, suffix="activation_event")
    async with postgres_session_factory() as session:
        activation = await CustomerActivationRepository(
            session
        ).get_or_create_for_update(user.id)
        await session.commit()
        activation_id = activation.id

    idempotency_key = f"activation-event:{uuid4().hex}"
    backend_pid_queue: asyncio.Queue[int] = asyncio.Queue()

    async def append(caller: str):
        async with postgres_session_factory() as session:
            backend_pid = await session.scalar(select(func.pg_backend_pid()))
            assert backend_pid is not None
            await backend_pid_queue.put(backend_pid)
            event = await ActivationEventRepository(session).append(
                user_id=user.id,
                activation_id=activation_id,
                event_type=f"profile_confirmed_{caller}",
                idempotency_key=idempotency_key,
                metadata={"caller": caller},
            )
            await session.commit()
            return event.id, event.event_type, event.event_metadata

    async with postgres_session_factory() as gate_session:
        await gate_session.execute(
            text("LOCK TABLE activation_events IN SHARE MODE")
        )
        tasks = [
            asyncio.create_task(append("a")),
            asyncio.create_task(append("b")),
        ]
        backend_pids = (
            await backend_pid_queue.get(),
            await backend_pid_queue.get(),
        )
        await _wait_for_two_lock_waiters(
            gate_session,
            backend_pids=backend_pids,
        )
        await gate_session.commit()
        returned_events = await asyncio.gather(*tasks)

    assert returned_events[0] == returned_events[1]
    assert returned_events[0][1:] in (
        ("profile_confirmed_a", {"caller": "a"}),
        ("profile_confirmed_b", {"caller": "b"}),
    )
    async with postgres_session_factory() as session:
        durable_events = list(
            (
                await session.execute(
                    select(ActivationEvent).where(
                        ActivationEvent.idempotency_key == idempotency_key
                    )
                )
            ).scalars()
        )
    assert len(durable_events) == 1
    assert durable_events[0].id == returned_events[0][0]


@pytest.mark.anyio
async def test_verification_expiry_workers_skip_locked_users_before_activation_rows(
    postgres_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime(2026, 7, 18, 12, tzinfo=UTC)
    seeded: list[tuple[User, CustomerActivation]] = []
    for index, suffix in enumerate(
        ("verification_expiry_a", "verification_expiry_b")
    ):
        user = await _create_user(postgres_session_factory, suffix=suffix)
        async with postgres_session_factory() as session:
            expires_at = now - timedelta(seconds=2 - index)
            activation = CustomerActivation(
                user_id=user.id,
                verification_window_started_at=expires_at - timedelta(minutes=10),
                verification_window_expires_at=expires_at,
                verification_status="open",
            )
            session.add(activation)
            await session.commit()
            seeded.append((user, activation))

    async def worker_b_expire_one() -> int:
        async with postgres_session_factory() as session:
            return await ForwardingVerificationService(
                session,
                now_provider=lambda: now,
            ).expire_batch(limit=1)

    async with postgres_session_factory() as worker_a_session:
        worker_a_user_id = await worker_a_session.scalar(
            build_expiry_user_claim_statement(now=now, limit=1)
        )
        assert worker_a_user_id == seeded[0][0].id

        worker_b_result = await asyncio.wait_for(
            worker_b_expire_one(),
            timeout=2,
        )
        assert worker_b_result == 1
        await worker_a_session.rollback()

    async with postgres_session_factory() as session:
        statuses = {
            activation.user_id: activation.verification_status
            for activation in await session.scalars(
                select(CustomerActivation)
            )
        }
        assert statuses == {
            seeded[0][0].id: "open",
            seeded[1][0].id: "expired",
        }
        assert await session.scalar(
            select(func.count())
            .select_from(ActivationEvent)
            .where(ActivationEvent.event_type == "verification_window_expired")
        ) == 1
