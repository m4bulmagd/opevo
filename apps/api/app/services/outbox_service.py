from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.outbox_event import OutboxEvent
from app.repositories.outbox_repository import OutboxRepository


SUPPORTED_OUTBOX_TOPICS = frozenset(
    {
        "phone.provision",
        "phone.enable",
        "phone.disable",
        "livekit.dispatch",
        "livekit.verification_dispatch",
        "summary.generate",
        "recording.stop",
    }
)

REFERENCE_PAYLOAD_FIELDS = {
    "phone.provision": frozenset({"user_id"}),
    "phone.enable": frozenset({"user_id"}),
    "phone.disable": frozenset({"user_id"}),
    "livekit.dispatch": frozenset({"call_id"}),
    "livekit.verification_dispatch": frozenset(
        {"activation_id", "session_id", "room_name"}
    ),
    "summary.generate": frozenset({"call_id"}),
    "recording.stop": frozenset({"call_id"}),
}


class OutboxPayloadError(ValueError):
    pass


class OutboxIdempotencyConflictError(RuntimeError):
    pass


def validate_outbox_payload(topic: str, payload: dict) -> None:
    required_fields = REFERENCE_PAYLOAD_FIELDS.get(topic)
    if required_fields is None:
        raise OutboxPayloadError("Unsupported outbox topic")
    if not isinstance(payload, dict) or set(payload) != required_fields:
        raise OutboxPayloadError("Outbox payload must contain references only")
    for field in required_fields:
        value = payload.get(field)
        if not isinstance(value, str) or not value:
            raise OutboxPayloadError("Outbox reference is invalid")
        if field == "room_name":
            continue
        try:
            UUID(value)
        except (TypeError, ValueError):
            raise OutboxPayloadError("Outbox reference is invalid") from None


class OutboxService:
    def __init__(self, session: AsyncSession) -> None:
        self.repository = OutboxRepository(session)

    async def add(
        self,
        *,
        topic: str,
        aggregate_type: str,
        aggregate_id: UUID,
        idempotency_key: str,
        payload: dict,
    ) -> OutboxEvent:
        validate_outbox_payload(topic, payload)
        event = await self.repository.add_once(
            topic=topic,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            idempotency_key=idempotency_key,
            payload=payload,
            next_attempt_at=datetime.now(UTC),
        )
        if (
            event.topic != topic
            or event.aggregate_type != aggregate_type
            or event.aggregate_id != aggregate_id
            or event.payload != payload
        ):
            raise OutboxIdempotencyConflictError(
                "Outbox idempotency key already identifies different content"
            )
        return event
