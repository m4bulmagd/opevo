from typing import Literal

import pytest

from app.core.provider_failures import (
    ProviderFailure,
    ProviderFailureDisposition,
    ProviderOperation,
)
from app.providers.livekit_dispatch.base import LiveKitDispatch
from app.workers.outbox.failures import OutboxDeliveryError
from app.workers.outbox._livekit_delivery import ensure_livekit_dispatch


def _dispatch(dispatch_id: str) -> LiveKitDispatch:
    return LiveKitDispatch(
        id=dispatch_id,
        agent_name="worker",
        room="room",
        metadata="metadata",
    )


def _provider_failure(
    *,
    operation: ProviderOperation,
    disposition: ProviderFailureDisposition,
) -> ProviderFailure:
    return ProviderFailure(
        provider="livekit",
        operation=operation,
        disposition=disposition,
        error_class="timeout" if disposition == "retryable" else "authentication",
    )


class _Provider:
    def __init__(
        self,
        trace: list[str],
        *,
        list_results: list[list[LiveKitDispatch] | Exception] | None = None,
        create_result: LiveKitDispatch | Exception | None = None,
    ) -> None:
        self.trace = trace
        self.list_results = list_results or [[]]
        self.create_result = create_result
        self.list_requests: list[str] = []
        self.create_requests: list[dict[str, str]] = []

    async def list_dispatches(self, *, room_name: str) -> list[LiveKitDispatch]:
        self.trace.append("list")
        self.list_requests.append(room_name)
        result = self.list_results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    async def create_dispatch(
        self,
        *,
        agent_name: str,
        room_name: str,
        metadata: str,
    ) -> LiveKitDispatch:
        self.trace.append("create")
        self.create_requests.append(
            {
                "agent_name": agent_name,
                "room_name": room_name,
                "metadata": metadata,
            }
        )
        if isinstance(self.create_result, Exception):
            raise self.create_result
        if self.create_result is not None:
            return self.create_result
        return LiveKitDispatch(
            id="created",
            agent_name=agent_name,
            room=room_name,
            metadata=metadata,
        )


def _revalidator(
    trace: list[str],
    *,
    fail_at: int | None = None,
    error: OutboxDeliveryError | None = None,
):
    validation_count = 0

    async def revalidate_account() -> None:
        nonlocal validation_count
        validation_count += 1
        trace.append("validate")
        if fail_at == validation_count:
            assert error is not None
            raise error

    return revalidate_account


def _reconciler(trace: list[str]):
    def reconcile(dispatches: list[LiveKitDispatch]) -> LiveKitDispatch | None:
        trace.append(
            f"reconcile({','.join(dispatch.id for dispatch in dispatches) or 'empty'})"
        )
        return dispatches[0] if dispatches else None

    return reconcile


async def _ensure(
    provider: _Provider,
    trace: list[str],
    *,
    persisted_dispatch_id: str | None = None,
    revalidate_account=None,
    reconcile=None,
) -> LiveKitDispatch:
    return await ensure_livekit_dispatch(
        provider=provider,
        room_name="room",
        worker_name="worker",
        metadata="metadata",
        persisted_dispatch_id=persisted_dispatch_id,
        revalidate_account=revalidate_account or _revalidator(trace),
        reconcile=reconcile or _reconciler(trace),
    )


@pytest.mark.anyio
async def test_existing_dispatch_is_returned_without_create() -> None:
    trace: list[str] = []
    existing = _dispatch("existing")
    provider = _Provider(trace, list_results=[[existing]])

    dispatch = await _ensure(provider, trace)

    assert dispatch is existing
    assert provider.list_requests == ["room"]
    assert trace == ["validate", "list", "validate", "reconcile(existing)"]


@pytest.mark.anyio
async def test_missing_dispatch_is_created_and_reconciled() -> None:
    trace: list[str] = []
    created = _dispatch("created")
    provider = _Provider(trace, create_result=created)

    dispatch = await _ensure(provider, trace)

    assert dispatch is created
    assert provider.list_requests == ["room"]
    assert provider.create_requests == [
        {"agent_name": "worker", "room_name": "room", "metadata": "metadata"}
    ]
    assert trace == [
        "validate",
        "list",
        "validate",
        "reconcile(empty)",
        "validate",
        "create",
        "reconcile(created)",
    ]


@pytest.mark.anyio
async def test_retryable_create_failure_relists_and_recovers() -> None:
    trace: list[str] = []
    recovered = _dispatch("recovered")
    provider = _Provider(
        trace,
        list_results=[[], [recovered]],
        create_result=_provider_failure(
            operation="create_dispatch",
            disposition="retryable",
        ),
    )

    dispatch = await _ensure(provider, trace)

    assert dispatch is recovered
    assert provider.list_requests == ["room", "room"]
    assert trace == [
        "validate",
        "list",
        "validate",
        "reconcile(empty)",
        "validate",
        "create",
        "list",
        "validate",
        "reconcile(recovered)",
    ]


@pytest.mark.anyio
async def test_retryable_create_failure_without_recovery_is_retryable() -> None:
    trace: list[str] = []
    retryable_failure = _provider_failure(
        operation="create_dispatch",
        disposition="retryable",
    )
    provider = _Provider(
        trace,
        list_results=[[], []],
        create_result=retryable_failure,
    )

    with pytest.raises(OutboxDeliveryError) as caught:
        await _ensure(provider, trace)

    assert caught.value.error_code == "provider_retryable"
    assert caught.value.retryable is True
    assert caught.value.exhaustible is True
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is retryable_failure
    assert caught.value.__suppress_context__ is True
    assert provider.list_requests == ["room", "room"]
    assert trace == [
        "validate",
        "list",
        "validate",
        "reconcile(empty)",
        "validate",
        "create",
        "list",
        "validate",
        "reconcile(empty)",
    ]


@pytest.mark.anyio
async def test_terminal_create_failure_does_not_relist() -> None:
    trace: list[str] = []
    terminal_failure = _provider_failure(
        operation="create_dispatch",
        disposition="terminal",
    )
    provider = _Provider(trace, create_result=terminal_failure)

    with pytest.raises(OutboxDeliveryError) as caught:
        await _ensure(provider, trace)

    assert caught.value.error_code == "provider_terminal"
    assert caught.value.retryable is False
    assert caught.value.exhaustible is True
    assert caught.value.__cause__ is terminal_failure
    assert provider.list_requests == ["room"]
    assert trace == [
        "validate",
        "list",
        "validate",
        "reconcile(empty)",
        "validate",
        "create",
    ]


@pytest.mark.anyio
async def test_initial_untyped_provider_error_propagates_unchanged() -> None:
    trace: list[str] = []
    untyped_error = RuntimeError("UNTYPED_PROVIDER_DEFECT_SENTINEL")
    provider = _Provider(
        trace,
        list_results=[untyped_error],
    )

    with pytest.raises(RuntimeError) as caught:
        await _ensure(provider, trace)

    assert caught.value is untyped_error
    assert provider.list_requests == ["room"]
    assert trace == ["validate", "list"]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("disposition", "expected_code", "expected_retryable"),
    [
        ("retryable", "provider_retryable", True),
        ("terminal", "provider_terminal", False),
    ],
)
async def test_initial_list_provider_failure_preserves_its_policy_and_cause(
    disposition: ProviderFailureDisposition,
    expected_code: Literal["provider_retryable", "provider_terminal"],
    expected_retryable: bool,
) -> None:
    trace: list[str] = []
    initial_failure = _provider_failure(
        operation="list_dispatches",
        disposition=disposition,
    )
    provider = _Provider(trace, list_results=[initial_failure])

    with pytest.raises(OutboxDeliveryError) as caught:
        await _ensure(provider, trace)

    assert caught.value.error_code == expected_code
    assert caught.value.retryable is expected_retryable
    assert caught.value.exhaustible is True
    assert caught.value.__cause__ is initial_failure
    assert provider.list_requests == ["room"]
    assert trace == ["validate", "list"]


@pytest.mark.anyio
async def test_create_untyped_provider_error_propagates_unchanged() -> None:
    trace: list[str] = []
    untyped_error = RuntimeError("UNTYPED_PROVIDER_DEFECT_SENTINEL")
    provider = _Provider(trace, create_result=untyped_error)

    with pytest.raises(RuntimeError) as caught:
        await _ensure(provider, trace)

    assert caught.value is untyped_error
    assert provider.list_requests == ["room"]
    assert trace == [
        "validate",
        "list",
        "validate",
        "reconcile(empty)",
        "validate",
        "create",
    ]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("disposition", "expected_code", "expected_retryable"),
    [
        ("retryable", "provider_retryable", True),
        ("terminal", "provider_terminal", False),
    ],
)
async def test_recovery_list_provider_failure_preserves_its_policy_and_cause(
    disposition: ProviderFailureDisposition,
    expected_code: Literal["provider_retryable", "provider_terminal"],
    expected_retryable: bool,
) -> None:
    trace: list[str] = []
    recovery_failure = _provider_failure(
        operation="list_dispatches",
        disposition=disposition,
    )
    provider = _Provider(
        trace,
        list_results=[[], recovery_failure],
        create_result=_provider_failure(
            operation="create_dispatch",
            disposition="retryable",
        ),
    )

    with pytest.raises(OutboxDeliveryError) as caught:
        await _ensure(provider, trace)

    assert caught.value.error_code == expected_code
    assert caught.value.retryable is expected_retryable
    assert caught.value.exhaustible is True
    assert caught.value.__cause__ is recovery_failure
    assert provider.list_requests == ["room", "room"]
    assert trace == [
        "validate",
        "list",
        "validate",
        "reconcile(empty)",
        "validate",
        "create",
        "list",
    ]


@pytest.mark.anyio
async def test_recovery_list_untyped_provider_error_propagates_unchanged() -> None:
    trace: list[str] = []
    untyped_error = RuntimeError("UNTYPED_PROVIDER_DEFECT_SENTINEL")
    provider = _Provider(
        trace,
        list_results=[[], untyped_error],
        create_result=_provider_failure(
            operation="create_dispatch",
            disposition="retryable",
        ),
    )

    with pytest.raises(RuntimeError) as caught:
        await _ensure(provider, trace)

    assert caught.value is untyped_error
    assert provider.list_requests == ["room", "room"]
    assert trace == [
        "validate",
        "list",
        "validate",
        "reconcile(empty)",
        "validate",
        "create",
        "list",
    ]


@pytest.mark.anyio
async def test_created_dispatch_rejected_by_reconciliation_is_retryable() -> None:
    trace: list[str] = []
    created = _dispatch("created")
    provider = _Provider(trace, create_result=created)

    def reconcile(dispatches: list[LiveKitDispatch]) -> LiveKitDispatch | None:
        trace.append(
            f"reconcile({','.join(dispatch.id for dispatch in dispatches) or 'empty'})"
        )
        return None

    with pytest.raises(OutboxDeliveryError) as caught:
        await _ensure(provider, trace, reconcile=reconcile)

    assert caught.value.error_code == "provider_retryable"
    assert caught.value.retryable is True
    assert caught.value.exhaustible is True
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert caught.value.__suppress_context__ is False
    assert provider.list_requests == ["room"]
    assert trace == [
        "validate",
        "list",
        "validate",
        "reconcile(empty)",
        "validate",
        "create",
        "reconcile(created)",
    ]


@pytest.mark.anyio
async def test_persisted_identity_without_provider_match_is_conflict() -> None:
    trace: list[str] = []
    provider = _Provider(trace)

    with pytest.raises(OutboxDeliveryError) as caught:
        await _ensure(provider, trace, persisted_dispatch_id="persisted")

    assert caught.value.error_code == "dispatch_conflict"
    assert caught.value.retryable is False
    assert caught.value.exhaustible is True
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert caught.value.__suppress_context__ is False
    assert provider.list_requests == ["room"]
    assert trace == ["validate", "list", "validate", "reconcile(empty)"]


@pytest.mark.anyio
async def test_reconciliation_conflict_propagates_without_create() -> None:
    trace: list[str] = []
    provider = _Provider(trace, list_results=[[_dispatch("existing")]])
    reconciliation_error = OutboxDeliveryError("dispatch_conflict", retryable=False)

    def reconcile(_dispatches: list[LiveKitDispatch]) -> LiveKitDispatch | None:
        trace.append("reconcile(existing)")
        raise reconciliation_error

    with pytest.raises(OutboxDeliveryError) as caught:
        await _ensure(provider, trace, reconcile=reconcile)

    assert caught.value is reconciliation_error
    assert provider.list_requests == ["room"]
    assert trace == ["validate", "list", "validate", "reconcile(existing)"]


@pytest.mark.anyio
async def test_lifecycle_invalidation_before_list_prevents_provider_io() -> None:
    trace: list[str] = []
    provider = _Provider(trace)
    lifecycle_error = OutboxDeliveryError("dispatch_ineligible", retryable=False)

    with pytest.raises(OutboxDeliveryError) as caught:
        await _ensure(
            provider,
            trace,
            revalidate_account=_revalidator(
                trace,
                fail_at=1,
                error=lifecycle_error,
            ),
        )

    assert caught.value is lifecycle_error
    assert provider.list_requests == []
    assert trace == ["validate"]


@pytest.mark.anyio
async def test_lifecycle_invalidation_after_recovery_list_prevents_persistence_result() -> None:
    trace: list[str] = []
    provider = _Provider(
        trace,
        list_results=[[], [_dispatch("recovered")]],
        create_result=_provider_failure(
            operation="create_dispatch",
            disposition="retryable",
        ),
    )
    lifecycle_error = OutboxDeliveryError("dispatch_ineligible", retryable=False)

    with pytest.raises(OutboxDeliveryError) as caught:
        await _ensure(
            provider,
            trace,
            revalidate_account=_revalidator(
                trace,
                fail_at=4,
                error=lifecycle_error,
            ),
        )

    assert caught.value is lifecycle_error
    assert provider.list_requests == ["room", "room"]
    assert trace == [
        "validate",
        "list",
        "validate",
        "reconcile(empty)",
        "validate",
        "create",
        "list",
        "validate",
    ]
