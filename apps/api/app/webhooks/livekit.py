from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_session
from app.services.livekit_dispatch_service import LiveKitDispatchService


router = APIRouter(prefix="/webhooks", tags=["livekit"])


class LiveKitDispatchClient:
    async def create_dispatch(self, *, room_name: str, metadata: str) -> None:
        from livekit import api

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
                    agent_name=settings.livekit_agent_name,
                    room=room_name,
                    metadata=metadata,
                )
            )
        finally:
            await lkapi.aclose()


def get_dispatch_client() -> LiveKitDispatchClient:
    return LiveKitDispatchClient()


def get_webhook_receiver():
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

    if event_payload["event"] == "participant_joined":
        service = LiveKitDispatchService(session, dispatch_client=dispatch_client)
        await service.handle_participant_joined(event_payload)
    else:
        await session.commit()

    return Response(status_code=status.HTTP_202_ACCEPTED)
