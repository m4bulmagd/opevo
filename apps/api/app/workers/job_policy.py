import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import wraps
from typing import Any, TypeVar

from arq import Retry
from sqlalchemy.exc import (
    DBAPIError,
    DisconnectionError,
    IntegrityError,
    OperationalError,
    ProgrammingError,
    TimeoutError as SQLAlchemyTimeoutError,
)

from app.core.observability import Observability, instrument_job


ResultT = TypeVar("ResultT")
JobFunction = Callable[..., Awaitable[ResultT]]


@dataclass(frozen=True, slots=True)
class JobPolicy:
    arq_name: str
    job_name: str
    semantic_timeout_seconds: int | float
    max_tries: int
    retry_delays_seconds: tuple[int, ...] = ()

    @property
    def hard_timeout_seconds(self) -> int | float:
        return self.semantic_timeout_seconds + 5


CALL_FINALIZATION_POLICY = JobPolicy(
    "call_finalization_job", "call_finalization", 30, 3, (1, 5)
)
CALL_RECONCILIATION_POLICY = JobPolicy(
    "call_reconciliation_job", "call_reconciliation", 60, 1
)
OUTBOX_DELIVERY_POLICY = JobPolicy(
    "outbox_delivery_job", "outbox_delivery", 300, 1
)
OUTBOX_RECONCILIATION_POLICY = JobPolicy(
    "outbox_reconciliation_job", "outbox_reconciliation", 300, 1
)
VERIFICATION_EXPIRY_POLICY = JobPolicy(
    "verification_expiry_job", "verification_expiry", 60, 1
)


def is_retryable_call_finalization_error(error: BaseException) -> bool:
    if isinstance(
        error,
        (asyncio.CancelledError, IntegrityError, ProgrammingError),
    ):
        return False
    if isinstance(error, (TimeoutError, SQLAlchemyTimeoutError)):
        return True
    if isinstance(error, (OperationalError, DisconnectionError)):
        return True
    return isinstance(error, DBAPIError) and error.connection_invalidated is True


def apply_job_policy(
    function: JobFunction[ResultT],
    *,
    policy: JobPolicy,
    queue_class: str,
    observability_getter: Callable[[dict[str, Any]], Observability],
) -> JobFunction[ResultT]:
    @wraps(function)
    async def enforce_semantic_timeout(
        ctx: dict[str, Any],
        *args: Any,
        **kwargs: Any,
    ) -> ResultT:
        async with asyncio.timeout(policy.semantic_timeout_seconds):
            return await function(ctx, *args, **kwargs)

    observed_function = instrument_job(
        policy.job_name,
        queue_class=queue_class,
        observability_getter=observability_getter,
    )(enforce_semantic_timeout)

    @wraps(function)
    async def retry_adapter(
        ctx: dict[str, Any],
        *args: Any,
        **kwargs: Any,
    ) -> ResultT:
        try:
            return await observed_function(ctx, *args, **kwargs)
        except BaseException as error:
            attempt = ctx.get("job_try", 1)
            if (
                type(attempt) is int
                and 1 <= attempt <= len(policy.retry_delays_seconds)
                and attempt < policy.max_tries
                and is_retryable_call_finalization_error(error)
            ):
                raise Retry(defer=policy.retry_delays_seconds[attempt - 1]) from error
            raise

    return retry_adapter
