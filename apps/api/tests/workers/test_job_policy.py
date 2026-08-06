import asyncio
from dataclasses import FrozenInstanceError

import pytest
from arq import Retry
from sqlalchemy.exc import (
    DBAPIError,
    DisconnectionError,
    IntegrityError,
    OperationalError,
    ProgrammingError,
    TimeoutError as SQLAlchemyTimeoutError,
)

from app.core.observability import get_observability
from app.workers.job_policy import (
    CALL_FINALIZATION_POLICY,
    CALL_RECONCILIATION_POLICY,
    OUTBOX_DELIVERY_POLICY,
    OUTBOX_RECONCILIATION_POLICY,
    VERIFICATION_EXPIRY_POLICY,
    JobPolicy,
    apply_job_policy,
    is_retryable_call_finalization_error,
)


def _observability_getter(_ctx: dict):
    return get_observability()


def test_worker_job_policies_are_immutable_and_bounded() -> None:
    assert (
        CALL_FINALIZATION_POLICY,
        CALL_RECONCILIATION_POLICY,
        OUTBOX_DELIVERY_POLICY,
        OUTBOX_RECONCILIATION_POLICY,
        VERIFICATION_EXPIRY_POLICY,
    ) == (
        JobPolicy("call_finalization_job", "call_finalization", 30, 3, (1, 5)),
        JobPolicy("call_reconciliation_job", "call_reconciliation", 60, 1),
        JobPolicy("outbox_delivery_job", "outbox_delivery", 300, 1),
        JobPolicy("outbox_reconciliation_job", "outbox_reconciliation", 300, 1),
        JobPolicy("verification_expiry_job", "verification_expiry", 60, 1),
    )
    assert [
        policy.hard_timeout_seconds
        for policy in (
            CALL_FINALIZATION_POLICY,
            CALL_RECONCILIATION_POLICY,
            OUTBOX_DELIVERY_POLICY,
            OUTBOX_RECONCILIATION_POLICY,
            VERIFICATION_EXPIRY_POLICY,
        )
    ] == [35, 65, 305, 305, 65]
    with pytest.raises(FrozenInstanceError):
        CALL_FINALIZATION_POLICY.max_tries = 4  # type: ignore[misc]


@pytest.mark.parametrize(
    ("error", "retryable"),
    [
        (TimeoutError(), True),
        (SQLAlchemyTimeoutError(), True),
        (OperationalError("statement", {}, Exception()), True),
        (DisconnectionError(), True),
        (DBAPIError("statement", {}, Exception(), connection_invalidated=True), True),
        (IntegrityError("statement", {}, Exception()), False),
        (
            ProgrammingError(
                "statement",
                {},
                Exception(),
                connection_invalidated=True,
            ),
            False,
        ),
        (ValueError("bad payload"), False),
        (RuntimeError("defect"), False),
        (asyncio.CancelledError(), False),
    ],
)
def test_call_finalization_retry_classifier_is_narrow(
    error: BaseException,
    retryable: bool,
) -> None:
    assert is_retryable_call_finalization_error(error) is retryable


@pytest.mark.anyio
@pytest.mark.parametrize(("attempt", "defer_score"), [(1, 1000), (2, 5000)])
async def test_retryable_finalization_failures_use_bounded_backoff(
    attempt: int,
    defer_score: int,
) -> None:
    original = OperationalError("statement", {}, Exception())

    async def fail(_ctx: dict) -> None:
        raise original

    wrapped = apply_job_policy(
        fail,
        policy=CALL_FINALIZATION_POLICY,
        queue_class="call_lifecycle",
        observability_getter=_observability_getter,
    )

    with pytest.raises(Retry) as captured:
        await wrapped({"job_try": attempt})

    assert captured.value.defer_score == defer_score


@pytest.mark.anyio
async def test_third_retryable_finalization_failure_raises_original_error() -> None:
    original = OperationalError("statement", {}, Exception())

    async def fail(_ctx: dict) -> None:
        raise original

    wrapped = apply_job_policy(
        fail,
        policy=CALL_FINALIZATION_POLICY,
        queue_class="call_lifecycle",
        observability_getter=_observability_getter,
    )

    with pytest.raises(OperationalError) as captured:
        await wrapped({"job_try": 3})

    assert captured.value is original


@pytest.mark.anyio
@pytest.mark.parametrize("error", [ValueError("bad payload"), asyncio.CancelledError()])
async def test_non_retryable_finalization_failures_are_unchanged(
    error: BaseException,
) -> None:
    async def fail(_ctx: dict) -> None:
        raise error

    wrapped = apply_job_policy(
        fail,
        policy=CALL_FINALIZATION_POLICY,
        queue_class="call_lifecycle",
        observability_getter=_observability_getter,
    )

    with pytest.raises(type(error)) as captured:
        await wrapped({"job_try": 1})

    assert captured.value is error


@pytest.mark.anyio
async def test_invalidated_programming_error_is_not_converted_to_retry() -> None:
    original = ProgrammingError(
        "statement",
        {},
        Exception(),
        connection_invalidated=True,
    )

    async def fail(_ctx: dict) -> None:
        raise original

    wrapped = apply_job_policy(
        fail,
        policy=CALL_FINALIZATION_POLICY,
        queue_class="call_lifecycle",
        observability_getter=_observability_getter,
    )

    with pytest.raises(ProgrammingError) as captured:
        await wrapped({"job_try": 1})

    assert captured.value is original


@pytest.mark.anyio
async def test_blocked_job_becomes_timeout_error_at_semantic_bound() -> None:
    blocked = asyncio.Event()
    policy = JobPolicy("bounded_job", "verification_expiry", 0.01, 1)

    async def wait_forever(_ctx: dict) -> None:
        await blocked.wait()

    wrapped = apply_job_policy(
        wait_forever,
        policy=policy,
        queue_class="background",
        observability_getter=_observability_getter,
    )

    with pytest.raises(TimeoutError):
        await wrapped({"job_try": 1})
