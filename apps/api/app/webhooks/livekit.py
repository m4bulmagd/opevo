import logging

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_session
from app.repositories.agent_config_repository import AgentConfigRepository
from app.repositories.call_repository import CallRepository
from app.repositories.phone_number_repository import PhoneNumberRepository
from app.repositories.usage_repository import UsageRepository
from app.repositories.user_repository import UserRepository
from app.services.livekit_dispatch_service import LiveKitDispatchService
from app.services.livekit_recording_service import LiveKitRecordingService
from app.services.realtime_service import RealtimeService


router = APIRouter(prefix="/webhooks", tags=["livekit"])
logger = logging.getLogger(__name__)


class LiveKitDispatchClient:
    def __init__(self, livekit_api=None) -> None:
        self._livekit_api = livekit_api

    async def create_dispatch(self, *, room_name: str, metadata: str) -> None:
        from livekit import api

        lkapi = self._livekit_api
        if lkapi is None:
            settings = get_settings()
            if not settings.livekit_url or not settings.livekit_api_key or not settings.livekit_api_secret:
                raise ValueError("LiveKit settings are not configured")
            lkapi = api.LiveKitAPI(
                url=settings.livekit_url,
                api_key=settings.livekit_api_key,
                api_secret=settings.livekit_api_secret,
            )

        try:
            await lkapi.agent_dispatch.create_dispatch(
                api.CreateAgentDispatchRequest(
                    agent_name=get_settings().livekit_agent_name,
                    room=room_name,
                    metadata=metadata,
                )
            )
        finally:
            if self._livekit_api is None:
                await lkapi.aclose()


def get_dispatch_client(request: Request) -> LiveKitDispatchClient:
    livekit_api = getattr(request.app.state, "livekit_api", None)
    return LiveKitDispatchClient(livekit_api=livekit_api)


def get_realtime_service(request: Request) -> RealtimeService:
    return request.app.state.realtime_service


def get_webhook_receiver(request: Request):
    receiver = getattr(request.app.state, "livekit_webhook_receiver", None)
    if receiver is not None:
        return receiver
    from livekit import api

    settings = get_settings()
    verifier = api.TokenVerifier(settings.livekit_api_key, settings.livekit_api_secret)
    return api.WebhookReceiver(verifier)


@router.post("/livekit", status_code=status.HTTP_202_ACCEPTED)
async def handle_livekit_webhook(
    request: Request,
    session: AsyncSession = Depends(get_session),
    webhook_receiver=Depends(get_webhook_receiver),
    dispatch_client: LiveKitDispatchClient = Depends(get_dispatch_client),
    realtime_service: RealtimeService = Depends(get_realtime_service),
) -> Response:
    body = (await request.body()).decode("utf-8")
    event = webhook_receiver.receive(body, request.headers.get("authorization"))

    if isinstance(event, dict):
        event_payload = event
    else:
        event_payload = {
            "event": getattr(event, "event", None),
            "room": {"name": getattr(getattr(event, "room", None), "name", None)},
            "participant": {
                "identity": getattr(getattr(event, "participant", None), "identity", None),
                "kind": getattr(getattr(event, "participant", None), "kind", None),
                "attributes": dict(getattr(getattr(event, "participant", None), "attributes", {}) or {}),
            },
        }

    logger.info(
        "livekit webhook received event=%s participant_kind=%s",
        event_payload.get("event"),
        event_payload.get("participant", {}).get("kind"),
    )

    if event_payload["event"] in ("participant_joined", "participant_left"):
        service = LiveKitDispatchService(
            session,
            dispatch_client,
            phone_number_repository=PhoneNumberRepository(session),
            agent_config_repository=AgentConfigRepository(session),
            call_repository=CallRepository(session),
            user_repository=UserRepository(session),
            usage_repository=UsageRepository(session),
            realtime_service=realtime_service,
            recording_service=LiveKitRecordingService(),
        )
        if event_payload["event"] == "participant_joined":
            await service.handle_participant_joined(event_payload)
        else:
            await service.handle_participant_left(event_payload)
    else:
        await session.commit()

    return Response(status_code=status.HTTP_202_ACCEPTED)
