from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.activation_event import ActivationEvent
from app.models.customer_activation import CustomerActivation
from app.models.user import User
from app.workers import arq_worker
from app.workers.jobs.verification_expiry import verification_expiry_job
from app.services.forwarding_verification_service import (
    build_expiry_user_claim_statement,
)


FIXED_NOW = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)


async def _seed_window(
    db_session,
    *,
    status: str,
    expires_at: datetime,
) -> CustomerActivation:
    identity = uuid4().hex
    user = User(
        clerk_user_id=f"verification_expiry_{identity}",
        email=f"verification-expiry-{identity}@example.com",
    )
    db_session.add(user)
    await db_session.flush()
    activation = CustomerActivation(
        user_id=user.id,
        verification_window_started_at=expires_at - timedelta(minutes=10),
        verification_window_expires_at=expires_at,
        verification_status=status,
        verification_session_id=(
            str(uuid4()) if status == "claimed" else None
        ),
        verification_claimed_at=(
            expires_at - timedelta(minutes=1) if status == "claimed" else None
        ),
    )
    db_session.add(activation)
    await db_session.commit()
    return activation


def _context(db_session, *, now: datetime, batch_size: int = 100) -> dict:
    return {
        "session_factory": async_sessionmaker(
            db_session.bind,
            expire_on_commit=False,
        ),
        "verification_expiry_now": lambda: now,
        "verification_expiry_batch_size": batch_size,
    }


@pytest.mark.anyio
async def test_job_expires_open_window_at_exact_deadline(
    db_session,
) -> None:
    activation = await _seed_window(
        db_session,
        status="open",
        expires_at=FIXED_NOW,
    )
    activation_id = activation.id

    result = await verification_expiry_job(
        _context(db_session, now=FIXED_NOW)
    )

    db_session.expire_all()
    stored = await db_session.get(CustomerActivation, activation_id)
    assert result == {"expired": 1}
    assert stored is not None
    assert stored.verification_status == "expired"
    assert stored.last_failure_code == "verification_window_expired"
    assert await db_session.scalar(
        select(func.count())
        .select_from(ActivationEvent)
        .where(ActivationEvent.event_type == "verification_window_expired")
    ) == 1


@pytest.mark.anyio
async def test_job_preserves_claim_through_completion_grace(
    db_session,
) -> None:
    activation = await _seed_window(
        db_session,
        status="claimed",
        expires_at=FIXED_NOW,
    )
    activation_id = activation.id

    result = await verification_expiry_job(
        _context(
            db_session,
            now=FIXED_NOW + timedelta(minutes=2) - timedelta(microseconds=1),
        )
    )

    db_session.expire_all()
    stored = await db_session.get(CustomerActivation, activation_id)
    assert result == {"expired": 0}
    assert stored is not None
    assert stored.verification_status == "claimed"


@pytest.mark.anyio
async def test_job_expires_claim_at_exact_grace_deadline(
    db_session,
) -> None:
    activation = await _seed_window(
        db_session,
        status="claimed",
        expires_at=FIXED_NOW,
    )
    activation_id = activation.id

    result = await verification_expiry_job(
        _context(db_session, now=FIXED_NOW + timedelta(minutes=2))
    )

    db_session.expire_all()
    stored = await db_session.get(CustomerActivation, activation_id)
    assert result == {"expired": 1}
    assert stored is not None
    assert stored.verification_status == "expired"


@pytest.mark.anyio
async def test_duplicate_cron_execution_is_idempotent(
    db_session,
) -> None:
    await _seed_window(db_session, status="open", expires_at=FIXED_NOW)
    context = _context(db_session, now=FIXED_NOW)

    first = await verification_expiry_job(context)
    second = await verification_expiry_job(context)

    assert first == {"expired": 1}
    assert second == {"expired": 0}
    assert await db_session.scalar(
        select(func.count())
        .select_from(ActivationEvent)
        .where(ActivationEvent.event_type == "verification_window_expired")
    ) == 1


@pytest.mark.anyio
async def test_job_processes_a_bounded_batch(
    db_session,
) -> None:
    for _ in range(3):
        await _seed_window(db_session, status="open", expires_at=FIXED_NOW)
    context = _context(db_session, now=FIXED_NOW, batch_size=2)

    first = await verification_expiry_job(context)
    second = await verification_expiry_job(context)

    assert first == {"expired": 2}
    assert second == {"expired": 1}


def test_worker_registers_expiry_cron_once_per_minute() -> None:
    matches = [
        job
        for job in arq_worker.BackgroundWorkerSettings.cron_jobs
        if getattr(job, "name", None) == "verification_expiry_job"
    ]

    assert len(matches) == 1
    assert matches[0].minute == set(range(60))


def test_expiry_user_claim_sql_is_user_first_and_skips_locked_rows() -> None:
    statement = build_expiry_user_claim_statement(now=FIXED_NOW, limit=25)

    compiled = str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )

    assert "SELECT users.id" in compiled
    assert "FOR UPDATE OF users SKIP LOCKED" in compiled
    assert "LIMIT 25" in compiled
