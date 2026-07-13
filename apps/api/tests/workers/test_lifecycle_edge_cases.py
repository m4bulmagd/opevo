"""Edge cases for the durable two-phase call lifecycle."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models.call import Call
from app.models.outbox_event import OutboxEvent
from app.models.usage_ledger import UsageLedger
from app.repositories.call_repository import CallTransitionError
from app.services.call_lifecycle_service import CallLifecycleService


@pytest.mark.anyio
async def test_zero_duration_call_charges_one_minute(db_session, active_user) -> None:
    call = Call(
        user_id=active_user.id,
        status="ending",
        started_at=datetime.now(UTC),
        ended_at=datetime.now(UTC),
        duration_seconds=0,
    )
    db_session.add_all(
        [
            call,
            UsageLedger(
                user_id=active_user.id,
                event_type="subscription_activated",
                source_id="in_zero_duration",
                minutes_delta=5,
                balance_after=5,
            ),
        ]
    )
    await db_session.commit()
    lifecycle = CallLifecycleService(db_session)

    claim = await lifecycle.claim_finalization(call.id)
    result = await lifecycle.complete_finalization(
        call.id,
        generation=claim.generation,
    )

    assert result.minutes_charged == 1
    assert (await db_session.get(Call, call.id)).status == "completed"


@pytest.mark.anyio
async def test_missing_call_fails_without_partial_work(db_session) -> None:
    call_id = uuid4()
    lifecycle = CallLifecycleService(db_session)

    with pytest.raises(ValueError, match="Call not found"):
        await lifecycle.claim_finalization(call_id)

    assert await db_session.scalar(
        select(OutboxEvent).where(OutboxEvent.aggregate_id == call_id)
    ) is None


@pytest.mark.anyio
async def test_failed_call_cannot_accept_late_agent_completion(
    db_session,
    active_user,
) -> None:
    call = Call(
        user_id=active_user.id,
        status="failed",
        failure_code="dispatch_timeout",
    )
    db_session.add(call)
    await db_session.commit()
    call_id = call.id

    with pytest.raises(CallTransitionError, match="Failed call"):
        await CallLifecycleService(db_session).end_from_agent(
            call_id=call_id,
            duration_seconds=12,
        )

    await db_session.rollback()
    stored = await db_session.get(Call, call_id)
    assert stored.status == "failed"
    assert stored.ended_at is None


@pytest.mark.anyio
async def test_completed_call_retry_returns_existing_charge(
    db_session,
    active_user,
) -> None:
    call = Call(
        user_id=active_user.id,
        status="completed",
        duration_seconds=61,
        minutes_charged=2,
        finalization_attempt_count=1,
    )
    db_session.add(call)
    await db_session.commit()

    result = await CallLifecycleService(db_session).complete_finalization(
        call.id,
        generation=1,
    )

    assert result.already_completed is True
    assert result.minutes_charged == 2
