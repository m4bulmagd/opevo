import asyncio
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base
from app.models.call import Call
from app.models.usage_ledger import UsageLedger
from app.models.user import User
from app.repositories.usage_repository import UsageRepository
from app.services.usage_accounting_service import UsageAccountingService


@pytest_asyncio.fixture
async def usage_session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("Task 6 accounting tests require TEST_DATABASE_URL")
    if not database_url.startswith(("postgresql+asyncpg://", "postgresql://")):
        pytest.skip("TEST_DATABASE_URL must identify a PostgreSQL database")
    if database_url.startswith("postgresql://"):
        database_url = database_url.replace(
            "postgresql://",
            "postgresql+asyncpg://",
            1,
        )

    schema_name = f"task6_usage_{uuid4().hex}"
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


async def _seed_user(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    suffix: str,
) -> User:
    async with session_factory() as session:
        user = User(
            clerk_user_id=f"task6_{suffix}_{uuid4().hex}",
            email=f"task6_{suffix}_{uuid4().hex}@example.com",
        )
        session.add(user)
        await session.commit()
        return user


@pytest.mark.anyio
async def test_invoice_grant_is_idempotent_by_invoice_object_id(
    usage_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    user = await _seed_user(usage_session_factory, suffix="grant")
    invoice_id = f"in_{uuid4().hex}"

    async def grant():
        async with usage_session_factory() as session:
            result = await UsageAccountingService(session).grant_invoice(
                user_id=user.id,
                invoice_id=invoice_id,
                minutes=60,
            )
            await session.commit()
            return result

    results = await asyncio.gather(grant(), grant())
    first = next(result for result in results if not result.already_granted)
    duplicate = next(result for result in results if result.already_granted)

    assert first.already_granted is False
    assert first.first_activation is True
    assert first.ledger.event_type == "subscription_activated"
    assert first.ledger.source_id == invoice_id
    assert duplicate.already_granted is True
    assert duplicate.first_activation is True
    assert duplicate.ledger.id == first.ledger.id

    async with usage_session_factory() as session:
        grants = list(
            (
                await session.execute(
                    select(UsageLedger).where(UsageLedger.source_id == invoice_id)
                )
            ).scalars()
        )
    assert len(grants) == 1
    assert grants[0].balance_after == 60


@pytest.mark.anyio
async def test_two_call_debits_serialize_on_stable_user_scope(
    usage_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    user = await _seed_user(usage_session_factory, suffix="debit")

    async with usage_session_factory() as session:
        call_a = Call(user_id=user.id, status="awaiting_accounting")
        call_b = Call(user_id=user.id, status="awaiting_accounting")
        session.add_all(
            [
                UsageLedger(
                    user_id=user.id,
                    event_type="subscription_activated",
                    source_id=f"in_{uuid4().hex}",
                    minutes_delta=2,
                    balance_after=2,
                ),
                call_a,
                call_b,
            ]
        )
        await session.commit()
        call_ids = (call_a.id, call_b.id)

    async def debit(call_id: UUID):
        async with usage_session_factory() as session:
            result = await UsageAccountingService(session).debit_call(
                call_id=call_id,
                duration_seconds=60,
            )
            await session.commit()
            return result

    first, second = await asyncio.gather(*(debit(call_id) for call_id in call_ids))

    assert sorted([first.balance_after, second.balance_after]) == [0, 1]
    assert {first.user_id, second.user_id} == {user.id}
    assert first.minutes_charged == second.minutes_charged == 1

    async with usage_session_factory() as session:
        repository = UsageRepository(session)
        assert await repository.get_current_balance(user_id=user.id) == 0
        debit_count = await session.scalar(
            select(func.count())
            .select_from(UsageLedger)
            .where(
                UsageLedger.call_id.in_(call_ids),
                UsageLedger.event_type == "call_completed",
            )
        )
    assert debit_count == 2


@pytest.mark.anyio
async def test_call_debit_is_idempotent_and_capped_at_available_balance(
    usage_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    user = await _seed_user(usage_session_factory, suffix="capped")

    async with usage_session_factory() as session:
        call = Call(user_id=user.id, status="awaiting_accounting")
        session.add_all(
            [
                UsageLedger(
                    user_id=user.id,
                    event_type="subscription_activated",
                    source_id=f"in_{uuid4().hex}",
                    minutes_delta=1,
                    balance_after=1,
                ),
                call,
            ]
        )
        await session.commit()
        call_id = call.id

    async with usage_session_factory() as session:
        first = await UsageAccountingService(session).debit_call(
            call_id=call_id,
            duration_seconds=121,
        )
        await session.commit()

    async with usage_session_factory() as session:
        duplicate = await UsageAccountingService(session).debit_call(
            call_id=call_id,
            duration_seconds=999,
        )
        await session.commit()

    assert first.minutes_charged == 1
    assert first.balance_before == 1
    assert first.balance_after == 0
    assert first.already_debited is False
    assert duplicate.user_id == user.id
    assert duplicate.minutes_charged == 1
    assert duplicate.balance_before == 1
    assert duplicate.balance_after == 0
    assert duplicate.already_debited is True


@pytest.mark.anyio
async def test_latest_balance_uses_id_to_break_created_at_ties(
    usage_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    user = await _seed_user(usage_session_factory, suffix="ordering")
    created_at = datetime(2026, 1, 1, tzinfo=UTC)

    async with usage_session_factory() as session:
        session.add_all(
            [
                UsageLedger(
                    id=UUID(int=1),
                    user_id=user.id,
                    event_type="adjustment",
                    minutes_delta=1,
                    balance_after=1,
                    created_at=created_at,
                ),
                UsageLedger(
                    id=UUID(int=2),
                    user_id=user.id,
                    event_type="adjustment",
                    minutes_delta=1,
                    balance_after=2,
                    created_at=created_at,
                ),
            ]
        )
        await session.commit()

    async with usage_session_factory() as session:
        balance = await UsageRepository(session).get_current_balance(user_id=user.id)

    assert balance == 2


@pytest.mark.anyio
async def test_balance_order_follows_insert_order_not_transaction_start_order(
    usage_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    user = await _seed_user(usage_session_factory, suffix="causal_order")
    async with usage_session_factory() as session:
        call_started_early = Call(user_id=user.id, status="awaiting_accounting")
        call_started_late = Call(user_id=user.id, status="awaiting_accounting")
        session.add_all(
            [
                UsageLedger(
                    user_id=user.id,
                    event_type="subscription_activated",
                    source_id=f"in_{uuid4().hex}",
                    minutes_delta=2,
                    balance_after=2,
                ),
                call_started_early,
                call_started_late,
            ]
        )
        await session.commit()
        early_call_id = call_started_early.id
        late_call_id = call_started_late.id

    async with usage_session_factory() as early_session:
        await early_session.execute(text("SELECT 1"))

        async with usage_session_factory() as late_session:
            late = await UsageAccountingService(late_session).debit_call(
                call_id=late_call_id,
                duration_seconds=60,
            )
            await late_session.commit()

        early = await UsageAccountingService(early_session).debit_call(
            call_id=early_call_id,
            duration_seconds=60,
        )
        await early_session.commit()

    assert late.balance_after == 1
    assert early.balance_after == 0
    async with usage_session_factory() as session:
        assert await UsageRepository(session).get_current_balance(user_id=user.id) == 0


@pytest.mark.anyio
async def test_existing_cross_tenant_call_debit_fails_closed(
    usage_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    owner = await _seed_user(usage_session_factory, suffix="debit_owner")
    other = await _seed_user(usage_session_factory, suffix="debit_other")
    async with usage_session_factory() as session:
        call = Call(user_id=owner.id, status="completed", minutes_charged=1)
        session.add(call)
        await session.flush()
        session.add(
            UsageLedger(
                user_id=other.id,
                call_id=call.id,
                event_type="call_completed",
                minutes_delta=-1,
                balance_after=9,
            )
        )
        await session.commit()
        call_id = call.id

    async with usage_session_factory() as session:
        with pytest.raises(ValueError, match="owner"):
            await UsageAccountingService(session).debit_call(
                call_id=call_id,
                duration_seconds=60,
            )


@pytest.mark.anyio
async def test_invoice_grant_is_global_across_user_scopes_and_event_types(
    usage_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    first_user = await _seed_user(usage_session_factory, suffix="invoice_first")
    renewal_user = await _seed_user(usage_session_factory, suffix="invoice_renewal")
    invoice_id = f"in_shared_{uuid4().hex}"

    async with usage_session_factory() as session:
        session.add(
            UsageLedger(
                user_id=renewal_user.id,
                event_type="subscription_activated",
                source_id=f"in_prior_{uuid4().hex}",
                minutes_delta=60,
                balance_after=60,
            )
        )
        await session.commit()

    async def grant(user_id: UUID):
        async with usage_session_factory() as session:
            try:
                result = await UsageAccountingService(session).grant_invoice(
                    user_id=user_id,
                    invoice_id=invoice_id,
                    minutes=60,
                )
                await session.commit()
                return result
            except Exception as exc:
                await session.rollback()
                return exc

    outcomes = await asyncio.gather(
        grant(first_user.id),
        grant(renewal_user.id),
    )

    assert sum(not isinstance(outcome, Exception) for outcome in outcomes) == 1
    assert sum(isinstance(outcome, ValueError) for outcome in outcomes) == 1
    async with usage_session_factory() as session:
        matching = list(
            (
                await session.execute(
                    select(UsageLedger).where(UsageLedger.source_id == invoice_id)
                )
            ).scalars()
        )
    assert len(matching) == 1


@pytest.mark.anyio
async def test_invoice_source_lock_serializes_transactions_globally(
    usage_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    assert hasattr(UsageRepository, "lock_invoice_source")
    invoice_id = f"in_lock_{uuid4().hex}"

    async with usage_session_factory() as first_session:
        await UsageRepository(first_session).lock_invoice_source(
            source_id=invoice_id
        )

        second_session = usage_session_factory()
        await second_session.__aenter__()
        try:
            second_lock = asyncio.create_task(
                UsageRepository(second_session).lock_invoice_source(
                    source_id=invoice_id
                )
            )
            await asyncio.sleep(0.05)
            assert second_lock.done() is False

            await first_session.commit()
            await asyncio.wait_for(second_lock, timeout=1)
            await second_session.commit()
        finally:
            await second_session.__aexit__(None, None, None)
