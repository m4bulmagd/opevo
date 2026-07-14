from app.core.config import get_settings
from app.core.observability import get_observability, instrument_provider
from app.providers.livekit_dispatch.base import LiveKitDispatch


class LiveKitDispatchAPIProvider:
    def __init__(self, livekit_api=None, observability=None) -> None:
        self._livekit_api = livekit_api
        self.observability = observability or get_observability()

    @instrument_provider("livekit", "list_dispatches")
    async def list_dispatches(self, *, room_name: str) -> list[LiveKitDispatch]:
        livekit_api, owns_client = self._client()
        try:
            dispatches = await livekit_api.agent_dispatch.list_dispatch(room_name)
            return [self._to_dispatch(dispatch) for dispatch in dispatches]
        finally:
            if owns_client:
                await livekit_api.aclose()

    @instrument_provider("livekit", "create_dispatch")
    async def create_dispatch(
        self,
        *,
        agent_name: str,
        room_name: str,
        metadata: str,
    ) -> LiveKitDispatch:
        from livekit import api

        livekit_api, owns_client = self._client()
        try:
            dispatch = await livekit_api.agent_dispatch.create_dispatch(
                api.CreateAgentDispatchRequest(
                    agent_name=agent_name,
                    room=room_name,
                    metadata=metadata,
                )
            )
            return self._to_dispatch(dispatch)
        finally:
            if owns_client:
                await livekit_api.aclose()

    def _client(self):
        if self._livekit_api is not None:
            return self._livekit_api, False

        from livekit import api

        settings = get_settings()
        if (
            not settings.livekit_url
            or not settings.livekit_api_key
            or not settings.livekit_api_secret
        ):
            raise ValueError("LiveKit settings are not configured")
        return (
            api.LiveKitAPI(
                url=settings.livekit_url,
                api_key=settings.livekit_api_key,
                api_secret=settings.livekit_api_secret,
            ),
            True,
        )

    @staticmethod
    def _to_dispatch(dispatch) -> LiveKitDispatch:
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
            raise ValueError("Invalid LiveKit dispatch response")
        return LiveKitDispatch(
            id=dispatch_id,
            agent_name=agent_name,
            room=room,
            metadata=metadata,
            state=getattr(dispatch, "state", None),
        )
