from collections.abc import Awaitable, Callable

from app.core.provider_failures import ProviderFailure
from app.providers.livekit_dispatch.base import (
    LiveKitDispatch,
    LiveKitDispatchProvider,
)
from app.providers.livekit_dispatch.livekit import LiveKitDispatchConfigurationError
from app.workers.outbox.failures import (
    OutboxDeliveryError,
    provider_failure_delivery_error,
)


AccountRevalidator = Callable[[], Awaitable[None]]
DispatchReconciler = Callable[
    [list[LiveKitDispatch]],
    LiveKitDispatch | None,
]


async def ensure_livekit_dispatch(
    *,
    provider: LiveKitDispatchProvider,
    room_name: str,
    worker_name: str,
    metadata: str,
    persisted_dispatch_id: str | None,
    revalidate_account: AccountRevalidator,
    reconcile: DispatchReconciler,
) -> LiveKitDispatch:
    await revalidate_account()
    try:
        dispatches = await provider.list_dispatches(room_name=room_name)
    except LiveKitDispatchConfigurationError:
        raise OutboxDeliveryError(
            "dispatch_configuration",
            retryable=False,
        ) from None
    except ProviderFailure as error:
        raise provider_failure_delivery_error(error) from error

    await revalidate_account()
    dispatch = reconcile(dispatches)
    if dispatch is not None:
        return dispatch
    if persisted_dispatch_id is not None:
        raise OutboxDeliveryError(
            "dispatch_conflict",
            retryable=False,
        )

    await revalidate_account()
    try:
        created_dispatch = await provider.create_dispatch(
            agent_name=worker_name,
            room_name=room_name,
            metadata=metadata,
        )
    except LiveKitDispatchConfigurationError:
        raise OutboxDeliveryError(
            "dispatch_configuration",
            retryable=False,
        ) from None
    except ProviderFailure as error:
        if not error.retryable:
            raise provider_failure_delivery_error(error) from error
        try:
            dispatches = await provider.list_dispatches(room_name=room_name)
        except ProviderFailure as list_error:
            raise provider_failure_delivery_error(list_error) from list_error
        await revalidate_account()
        dispatch = reconcile(dispatches)
        if dispatch is None:
            raise OutboxDeliveryError(
                "provider_retryable",
                retryable=True,
            ) from None
        return dispatch

    dispatch = reconcile([created_dispatch])
    if dispatch is None:
        raise OutboxDeliveryError(
            "provider_retryable",
            retryable=True,
        )
    return dispatch
