import logging

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_session
from app.repositories.agent_config_repository import AgentConfigRepository
from app.repositories.call_repository import CallRepository
from app.repositories.phone_number_repository import PhoneNumberRepository
from app.repositories.subscription_repository import SubscriptionRepository
from app.repositories.usage_repository import UsageRepository
from app.repositories.user_repository import UserRepository
from app.repositories.webhook_event_repository import WebhookEventRepository
from app.services.livekit_dispatch_service import (
    LiveKitDispatchService,
    normalize_participant_kind,
)
from app.services.livekit_recording_service import LiveKitRecordingService
from app.services.realtime_service import RealtimeService


router = APIRouter(prefix="/webhooks", tags=["livekit"])
logger = logging.getLogger(__name__)


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


def convert_livekit_event(event) -> dict:
    if isinstance(event, dict):
        room = event.get("room") or {}
        participant = event.get("participant") or {}
        return {
            "id": event.get("id"),
            "event": event.get("event"),
            "room": {"name": room.get("name")},
            "participant": {
                "identity": participant.get("identity"),
                "kind": normalize_participant_kind(participant.get("kind")),
                "attributes": dict(participant.get("attributes") or {}),
            },
        }

    participant = getattr(event, "participant", None)
    return {
        "id": getattr(event, "id", None),
        "event": getattr(event, "event", None),
        "room": {"name": getattr(getattr(event, "room", None), "name", None)},
        "participant": {
            "identity": getattr(participant, "identity", None),
            "kind": normalize_participant_kind(getattr(participant, "kind", None)),
            "attributes": dict(getattr(participant, "attributes", {}) or {}),
        },
    }


@router.post("/livekit", status_code=status.HTTP_202_ACCEPTED)
async def handle_livekit_webhook(
    request: Request,
    session: AsyncSession = Depends(get_session),
    webhook_receiver=Depends(get_webhook_receiver),
    realtime_service: RealtimeService = Depends(get_realtime_service),
) -> Response:
    body = (await request.body()).decode("utf-8")
    event = webhook_receiver.receive(body, request.headers.get("authorization"))

    event_payload = convert_livekit_event(event)

    event_id = event_payload.get("id")
    event_type = event_payload.get("event")
    if not isinstance(event_id, str) or not event_id.strip():
        logger.warning(
            "livekit webhook rejected event=missing_event_id event_type=%s",
            event_type,
        )
        return Response(status_code=status.HTTP_202_ACCEPTED)

    is_new = await WebhookEventRepository(session).record_if_new(
        provider="livekit",
        external_event_id=event_id,
        event_type=str(event_type or "unknown"),
        payload={},
    )
    if not is_new:
        await session.commit()
        return Response(status_code=status.HTTP_202_ACCEPTED)

    logger.info(
        "livekit webhook received event=%s participant_kind=%s",
        event_type,
        event_payload.get("participant", {}).get("kind"),
    )

    if event_payload["event"] in ("participant_joined", "participant_left"):
        service = LiveKitDispatchService(
            session,
            phone_number_repository=PhoneNumberRepository(session),
            agent_config_repository=AgentConfigRepository(session),
            call_repository=CallRepository(session),
            user_repository=UserRepository(session),
            usage_repository=UsageRepository(session),
            subscription_repository=SubscriptionRepository(session),
            realtime_service=realtime_service,
            recording_service=LiveKitRecordingService(),
            arq_pool=getattr(request.app.state, "arq_pool", None),
        )
        if event_payload["event"] == "participant_joined":
            await service.handle_participant_joined(event_payload)
        else:
            await service.handle_participant_left(event_payload)
    else:
        await session.commit()

    return Response(status_code=status.HTTP_202_ACCEPTED)
