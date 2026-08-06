from collections.abc import Iterable

from livekit import api

from app.core.observability import Observability, instrument_provider
from app.core.provider_failures import ProviderFailure
from app.providers.livekit_dispatch.base import LiveKitDispatch
from app.providers.livekit_failures import livekit_failure_from_exception


class LiveKitDispatchConfigurationError(RuntimeError):
    pass


class _MalformedLiveKitDispatchResponse(ValueError):
    pass


class LiveKitDispatchAPIProvider:
    def __init__(self, *, livekit_api, observability: Observability) -> None:
        self._livekit_api = livekit_api
        self.observability = observability

    @instrument_provider("livekit", "list_dispatches")
    async def list_dispatches(self, *, room_name: str) -> list[LiveKitDispatch]:
        try:
            dispatches = await self._livekit_api.agent_dispatch.list_dispatch(room_name)
        except (api.TwirpError, TimeoutError, ConnectionError, OSError) as error:
            raise livekit_failure_from_exception(
                error,
                operation="list_dispatches",
            ) from error
        if not isinstance(dispatches, Iterable) or isinstance(dispatches, (str, bytes)):
            raise self._validation_failure("list_dispatches")
        try:
            return [self._to_dispatch(dispatch) for dispatch in dispatches]
        except _MalformedLiveKitDispatchResponse:
            raise self._validation_failure("list_dispatches") from None

    @instrument_provider("livekit", "create_dispatch")
    async def create_dispatch(
        self,
        *,
        agent_name: str,
        room_name: str,
        metadata: str,
    ) -> LiveKitDispatch:
        request = api.CreateAgentDispatchRequest(
            agent_name=agent_name,
            room=room_name,
            metadata=metadata,
        )
        try:
            dispatch = await self._livekit_api.agent_dispatch.create_dispatch(request)
        except (api.TwirpError, TimeoutError, ConnectionError, OSError) as error:
            raise livekit_failure_from_exception(
                error,
                operation="create_dispatch",
            ) from error
        try:
            return self._to_dispatch(dispatch)
        except _MalformedLiveKitDispatchResponse:
            raise self._validation_failure("create_dispatch") from None

    @staticmethod
    def _validation_failure(operation: str) -> ProviderFailure:
        return ProviderFailure(
            provider="livekit",
            operation=operation,  # type: ignore[arg-type]
            disposition="terminal",
            error_class="validation",
        )

    @staticmethod
    def _to_dispatch(dispatch: object) -> LiveKitDispatch:
        dispatch_id = getattr(dispatch, "id", None)
        agent_name = getattr(dispatch, "agent_name", None)
        room = getattr(dispatch, "room", None)
        metadata = getattr(dispatch, "metadata", None)
        if (
            not isinstance(dispatch_id, str)
            or not dispatch_id.strip()
            or not isinstance(agent_name, str)
            or not isinstance(room, str)
            or not room.strip()
            or not isinstance(metadata, str)
        ):
            raise _MalformedLiveKitDispatchResponse("Invalid LiveKit dispatch response")
        return LiveKitDispatch(
            id=dispatch_id,
            agent_name=agent_name,
            room=room,
            metadata=metadata,
            state=getattr(dispatch, "state", None),
        )
