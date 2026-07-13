from datetime import UTC, datetime

import pytest

from app.models.call import Call
from app.repositories import call_repository as call_repository_module
from app.repositories.call_repository import CallRepository


HAS_STATE_MACHINE = all(
    hasattr(call_repository_module, name)
    for name in ("CallTransitionError",)
)
CallTransitionError = getattr(
    call_repository_module,
    "CallTransitionError",
    RuntimeError,
)


def test_call_repository_exposes_state_machine_interface() -> None:
    assert HAS_STATE_MACHINE


@pytest.mark.skipif(not HAS_STATE_MACHINE, reason="state machine not implemented")
@pytest.mark.anyio
@pytest.mark.parametrize(
    ("source", "target"),
    [
        ("pending", "connected"),
        ("pending", "failed"),
        ("connected", "ending"),
        ("ending", "finalizing"),
        ("finalizing", "completed"),
        ("finalizing", "failed"),
    ],
)
async def test_legal_call_transition(
    db_session,
    active_user,
    source: str,
    target: str,
) -> None:
    call = Call(user_id=active_user.id, status=source)
    db_session.add(call)
    await db_session.flush()
    original_changed_at = call.state_changed_at

    result = await CallRepository(db_session).transition(
        call.id,
        from_states={source},
        to_state=target,
        failure_code="finalization_exhausted" if target == "failed" else None,
    )

    assert result.status == target
    assert result.state_changed_at is not None
    assert result.state_changed_at != original_changed_at


@pytest.mark.skipif(not HAS_STATE_MACHINE, reason="state machine not implemented")
@pytest.mark.anyio
@pytest.mark.parametrize(
    ("source", "target"),
    [
        ("completed", "finalizing"),
        ("failed", "connected"),
        ("pending", "ending"),
        ("connected", "completed"),
        ("ending", "failed"),
    ],
)
async def test_illegal_graph_edge_is_rejected_even_if_precondition_allows_it(
    db_session,
    active_user,
    source: str,
    target: str,
) -> None:
    call = Call(
        user_id=active_user.id,
        status=source,
        failure_code="legacy_failure" if source == "failed" else None,
    )
    db_session.add(call)
    await db_session.flush()

    with pytest.raises(CallTransitionError, match="Illegal call state transition"):
        await CallRepository(db_session).transition(
            call.id,
            from_states={source},
            to_state=target,
        )


@pytest.mark.skipif(not HAS_STATE_MACHINE, reason="state machine not implemented")
@pytest.mark.anyio
async def test_transition_requires_current_state_precondition(
    db_session,
    active_user,
) -> None:
    call = Call(user_id=active_user.id, status="connected")
    db_session.add(call)
    await db_session.flush()

    with pytest.raises(CallTransitionError, match="precondition"):
        await CallRepository(db_session).transition(
            call.id,
            from_states={"pending"},
            to_state="ending",
        )


@pytest.mark.skipif(not HAS_STATE_MACHINE, reason="state machine not implemented")
@pytest.mark.anyio
async def test_failed_transition_requires_allowlisted_failure_code(
    db_session,
    active_user,
) -> None:
    call = Call(user_id=active_user.id, status="pending")
    db_session.add(call)
    await db_session.flush()

    with pytest.raises(CallTransitionError, match="failure code"):
        await CallRepository(db_session).transition(
            call.id,
            from_states={"pending"},
            to_state="failed",
            failure_code="provider-secret-text",
        )


@pytest.mark.skipif(not HAS_STATE_MACHINE, reason="state machine not implemented")
@pytest.mark.anyio
async def test_nonfailed_transition_rejects_failure_code(
    db_session,
    active_user,
) -> None:
    call = Call(user_id=active_user.id, status="pending")
    db_session.add(call)
    await db_session.flush()

    with pytest.raises(CallTransitionError, match="failure code"):
        await CallRepository(db_session).transition(
            call.id,
            from_states={"pending"},
            to_state="connected",
            failure_code="dispatch_timeout",
        )


@pytest.mark.skipif(not HAS_STATE_MACHINE, reason="state machine not implemented")
@pytest.mark.anyio
async def test_connect_if_pending_updates_state_timestamp(
    db_session,
    active_user,
) -> None:
    before = datetime(2020, 1, 1, tzinfo=UTC)
    call = Call(
        user_id=active_user.id,
        status="pending",
        state_changed_at=before,
    )
    db_session.add(call)
    await db_session.flush()

    result = await CallRepository(db_session).connect_if_pending(call_id=call.id)

    assert result is not None
    assert result.status == "connected"
    assert result.state_changed_at != before


@pytest.mark.skipif(not HAS_STATE_MACHINE, reason="state machine not implemented")
@pytest.mark.anyio
async def test_new_usage_debit_requires_finalizing_call(
    db_session,
    active_user,
) -> None:
    from app.services.usage_accounting_service import UsageAccountingService

    call = Call(user_id=active_user.id, status="ending")
    db_session.add(call)
    await db_session.flush()

    with pytest.raises(ValueError, match="finalizing"):
        await UsageAccountingService(db_session).debit_call(
            call_id=call.id,
            duration_seconds=60,
        )
