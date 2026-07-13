"""Provider failures at post-call boundaries expose only safe error codes."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.call import Call
from app.models.call_message import CallMessage
from app.models.outbox_event import OutboxEvent
from app.workers.jobs.outbox_delivery import OutboxDeliveryError
from app.workers.jobs.outbox_topics import (
    deliver_recording_stop,
    deliver_summary_generate,
)


def _event(call_id, *, topic: str, aggregate_type: str) -> OutboxEvent:
    return OutboxEvent(
        id=uuid4(),
        idempotency_key=f"{topic}:{call_id}",
        topic=topic,
        aggregate_type=aggregate_type,
        aggregate_id=call_id,
        payload={"call_id": str(call_id)},
        status="processing",
        attempt_count=1,
        next_attempt_at=datetime.now(UTC),
    )


@pytest.mark.anyio
async def test_recording_provider_exception_is_translated_to_safe_retry(
    db_session,
    active_user,
) -> None:
    call = Call(
        user_id=active_user.id,
        status="completed",
        recording_egress_id="egress-safe-error",
    )
    db_session.add(call)
    await db_session.commit()
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    class SecretBearingProvider:
        async def ensure_stopped(self, _egress_id: str) -> None:
            raise RuntimeError("LIVEKIT_AUTHORIZATION_SENTINEL")

    with pytest.raises(OutboxDeliveryError) as exc_info:
        await deliver_recording_stop(
            {
                "session_factory": factory,
                "livekit_recording_provider": SecretBearingProvider(),
            },
            _event(
                call.id,
                topic="recording.stop",
                aggregate_type="call-recording",
            ),
        )

    assert exc_info.value.error_code == "provider_retryable"
    assert exc_info.value.retryable is True
    assert "LIVEKIT_AUTHORIZATION_SENTINEL" not in str(exc_info.value)


@pytest.mark.anyio
async def test_summary_provider_exception_is_translated_to_safe_retry(
    db_session,
    active_user,
) -> None:
    call = Call(user_id=active_user.id, status="completed")
    db_session.add(call)
    await db_session.flush()
    db_session.add(
        CallMessage(
            call_id=call.id,
            sequence_number=1,
            speaker="CALLER",
            text="Durable transcript",
        )
    )
    await db_session.commit()
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    class SecretBearingProvider:
        async def generate_summary(self, _transcript):
            raise RuntimeError("GEMINI_AUTHORIZATION_SENTINEL")

    with pytest.raises(OutboxDeliveryError) as exc_info:
        await deliver_summary_generate(
            {
                "session_factory": factory,
                "summary_provider": SecretBearingProvider(),
            },
            _event(
                call.id,
                topic="summary.generate",
                aggregate_type="call-summary",
            ),
        )

    assert exc_info.value.error_code == "provider_retryable"
    assert exc_info.value.retryable is True
    assert "GEMINI_AUTHORIZATION_SENTINEL" not in str(exc_info.value)
