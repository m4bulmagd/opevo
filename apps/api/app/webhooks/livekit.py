import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, cast

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_session
from app.core.observability import get_request_observability
from app.repositories.agent_config_repository import AgentConfigRepository
from app.repositories.call_repository import CallRepository
from app.repositories.phone_number_repository import PhoneNumberRepository
from app.repositories.subscription_repository import SubscriptionRepository
from app.repositories.usage_repository import UsageRepository
from app.repositories.user_repository import UserRepository
from app.repositories.webhook_event_repository import WebhookEventRepository
from app.providers.livekit_recording.livekit import (
    EgressObjectKeyEvidence,
    livekit_alias_values_equivalent,
    livekit_field_is_present,
    normalized_egress_object_key_evidence,
)
from app.services.livekit_dispatch_service import (
    LiveKitDispatchService,
    normalize_participant_kind,
)
from app.services.livekit_recording_service import LiveKitRecordingService
from app.services.recording_lifecycle_service import (
    RecordingEgressEventFact,
    RecordingLifecycleService,
)
from app.services.realtime_service import RealtimeService


router = APIRouter(prefix="/webhooks", tags=["livekit"])
logger = logging.getLogger(__name__)
EGRESS_EVENT_TYPES = frozenset({"egress_started", "egress_updated", "egress_ended"})
SAFE_LOG_EVENT_TYPES = EGRESS_EVENT_TYPES | frozenset(
    {"participant_joined", "participant_left", "room_finished"}
)
_MISSING = object()
_ALIAS_CONFLICT = object()


@dataclass(frozen=True)
class _ConvertedLiveKitEvent:
    payload: dict
    path_state: Literal["absent", "exact", "invalid"] | None = None


def _field(value: object, *names: str) -> object:
    candidates: list[object] = []
    try:
        is_mapping = isinstance(value, Mapping)
    except Exception:
        return _ALIAS_CONFLICT
    if is_mapping:
        mapping_value = cast(Mapping, value)
        for name in names:
            try:
                candidate = mapping_value[name]
            except KeyError:
                continue
            except Exception:
                return _ALIAS_CONFLICT
            candidates.append(candidate)
    else:
        for name in names:
            try:
                candidate = getattr(value, name, _MISSING)
                if candidate is _MISSING:
                    continue
                if not livekit_field_is_present(value, name):
                    continue
            except Exception:
                return _ALIAS_CONFLICT
            candidates.append(candidate)
    if not candidates:
        return _MISSING
    first = candidates[0]
    for candidate in candidates[1:]:
        if not livekit_alias_values_equivalent(first, candidate):
            return _ALIAS_CONFLICT
    return first


def get_realtime_service(request: Request) -> RealtimeService | None:
    return getattr(request.app.state, "realtime_service", None)


def get_webhook_receiver(request: Request):
    receiver = getattr(request.app.state, "livekit_webhook_receiver", None)
    if receiver is not None:
        return receiver
    from livekit import api

    settings = getattr(request.app.state, "settings", None) or get_settings()
    verifier = api.TokenVerifier(settings.livekit_api_key, settings.livekit_api_secret)
    return api.WebhookReceiver(verifier)


def _normalized_webhook_egress_object_key_evidence(
    egress: object,
    *,
    bucket_name: str,
    endpoint_url: str,
) -> EgressObjectKeyEvidence:
    """Fail closed on malformed objects received from the signed webhook SDK."""
    try:
        return normalized_egress_object_key_evidence(
            egress,
            bucket_name=bucket_name,
            endpoint_url=endpoint_url,
        )
    except Exception:
        return EgressObjectKeyEvidence("invalid")


def _convert_livekit_event(
    event: object,
    *,
    bucket_name: str,
    endpoint_url: str,
) -> _ConvertedLiveKitEvent:
    event_id = _field(event, "id")
    event_type = _field(event, "event")
    if event_id is _ALIAS_CONFLICT or event_type is _ALIAS_CONFLICT:
        return _invalid_converted_livekit_event()
    if type(event_type) is str and event_type in EGRESS_EVENT_TYPES:
        egress = _field(event, "egress_info", "egressInfo", "egress")
        evidence = (
            EgressObjectKeyEvidence("invalid")
            if egress is _ALIAS_CONFLICT
            else _normalized_webhook_egress_object_key_evidence(
                egress,
                bucket_name=bucket_name,
                endpoint_url=endpoint_url,
            )
        )
        return _ConvertedLiveKitEvent(
            payload={
                "id": _sanitized_webhook_string(event_id, max_length=255),
                "event": event_type,
                "egress": {
                    "egress_id": _sanitized_webhook_string(
                        _field(egress, "egress_id", "egressId"),
                        max_length=255,
                    ),
                    "room_name": _sanitized_webhook_string(
                        _field(egress, "room_name", "roomName"),
                        max_length=255,
                    ),
                    "status": _sanitized_egress_status(_field(egress, "status")),
                    "object_key": evidence.object_key,
                },
            },
            path_state=evidence.state,
        )

    try:
        is_mapping = isinstance(event, Mapping)
    except Exception:
        return _invalid_converted_livekit_event()
    if is_mapping:
        mapping_event = cast(Mapping, event)
        room = mapping_event.get("room") or {}
        participant = mapping_event.get("participant") or {}
        return _ConvertedLiveKitEvent(
            payload={
                "id": mapping_event.get("id"),
                "event": mapping_event.get("event"),
                "room": {"name": room.get("name")},
                "participant": {
                    "identity": participant.get("identity"),
                    "kind": normalize_participant_kind(participant.get("kind")),
                    "attributes": dict(participant.get("attributes") or {}),
                },
            }
        )

    participant = getattr(event, "participant", None)
    return _ConvertedLiveKitEvent(
        payload={
            "id": getattr(event, "id", None),
            "event": getattr(event, "event", None),
            "room": {"name": getattr(getattr(event, "room", None), "name", None)},
            "participant": {
                "identity": getattr(participant, "identity", None),
                "kind": normalize_participant_kind(getattr(participant, "kind", None)),
                "attributes": dict(getattr(participant, "attributes", {}) or {}),
            },
        }
    )


def _invalid_converted_livekit_event() -> _ConvertedLiveKitEvent:
    return _ConvertedLiveKitEvent(
        payload={"id": None, "event": None},
        path_state="invalid",
    )


def _sanitized_webhook_string(value: object, *, max_length: int) -> str | None:
    if not _bounded_webhook_string(value, max_length=max_length):
        return None
    return cast(str, value)


def _sanitized_egress_status(value: object) -> int | None:
    return value if type(value) is int and value in range(7) else None


def convert_livekit_event(
    event: object,
    *,
    bucket_name: str = "recordings",
    endpoint_url: str = "http://minio:9000",
) -> dict:
    return _convert_livekit_event(
        event,
        bucket_name=bucket_name,
        endpoint_url=endpoint_url,
    ).payload


@router.post("/livekit", status_code=status.HTTP_202_ACCEPTED)
async def handle_livekit_webhook(
    request: Request,
    session: AsyncSession = Depends(get_session),
    webhook_receiver=Depends(get_webhook_receiver),
    realtime_service: RealtimeService | None = Depends(get_realtime_service),
) -> Response:
    started = time.monotonic()
    outcome = "rejected"
    telemetry = get_request_observability(request)
    try:
        body = (await request.body()).decode("utf-8")
        event = webhook_receiver.receive(body, request.headers.get("authorization"))
        request_state = getattr(getattr(request, "app", None), "state", None)
        settings = getattr(request_state, "settings", None) or get_settings()
        converted = _convert_livekit_event(
            event,
            bucket_name=settings.storage_bucket_name,
            endpoint_url=settings.s3_endpoint_url or "http://minio:9000",
        )
        event_payload = converted.payload

        event_id = event_payload.get("id")
        event_type = event_payload.get("event")
        if not _bounded_webhook_string(event_id, max_length=255):
            logger.warning(
                "livekit webhook rejected event=missing_event_id event_type=%s",
                _safe_event_type(event_type),
            )
            return Response(status_code=status.HTTP_202_ACCEPTED)
        if not _bounded_webhook_string(event_type, max_length=100):
            logger.warning("livekit webhook rejected event=invalid_event_type")
            return Response(status_code=status.HTTP_202_ACCEPTED)
        event_id = cast(str, event_id)
        event_type = cast(str, event_type)

        outcome = "error"
        is_new = await WebhookEventRepository(session).record_if_new(
            provider="livekit",
            external_event_id=event_id,
            event_type=event_type,
            payload={},
        )
        if not is_new:
            await session.commit()
            outcome = "duplicate"
            return Response(status_code=status.HTTP_202_ACCEPTED)

        logger.info(
            "livekit webhook received event=%s",
            _safe_event_type(event_type),
        )

        if event_type in EGRESS_EVENT_TYPES:
            egress = event_payload.get("egress", {})
            lifecycle_outcome = await RecordingLifecycleService(
                session
            ).accept_egress_event(
                RecordingEgressEventFact(
                    external_event_id=event_id,
                    event_type=cast(
                        Literal[
                            "egress_started",
                            "egress_updated",
                            "egress_ended",
                        ],
                        event_type,
                    ),
                    egress_id=cast(str, egress.get("egress_id")),
                    room_name=cast(str, egress.get("room_name")),
                    status=cast(int, egress.get("status")),
                    object_key=cast(str | None, egress.get("object_key")),
                    object_key_evidence=converted.path_state or "invalid",
                )
            )
            await session.commit()
            if lifecycle_outcome in {"missing", "mismatch", "conflict"}:
                telemetry.record_recording_webhook_mismatch(lifecycle_outcome)
            await _best_effort_outbox_wakeup(request)
        elif event_type in ("participant_joined", "participant_left"):
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
            if event_type == "participant_joined":
                await service.handle_participant_joined(event_payload)
            else:
                await service.handle_participant_left(event_payload)
        else:
            await session.commit()

        outcome = "accepted"
        return Response(status_code=status.HTTP_202_ACCEPTED)
    except Exception:
        if outcome == "error":
            await session.rollback()
        raise
    finally:
        telemetry.record_webhook("livekit", outcome, time.monotonic() - started)


def _bounded_webhook_string(value: object, *, max_length: int) -> bool:
    return (
        type(value) is str
        and bool(value.strip())
        and len(value) <= max_length
        and "\x00" not in value
    )


def _safe_event_type(value: object) -> str:
    if type(value) is str and value in SAFE_LOG_EVENT_TYPES:
        return value
    return "unknown"


async def _best_effort_outbox_wakeup(request: Request) -> None:
    arq_pool = getattr(request.app.state, "arq_pool", None)
    if arq_pool is None:
        return
    try:
        await arq_pool.enqueue_job("outbox_delivery_job", {})
    except Exception:
        logger.warning(
            "outbox wakeup enqueue failed operation=livekit_egress_webhook "
            "error_type=unknown"
        )
