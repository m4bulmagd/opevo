from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.call import Call
from app.models.call_message import CallMessage
from app.models.outbox_event import OutboxEvent
from app.core.provider_failures import ProviderFailure
from app.providers.summaries.base import StructuredSummary
from app.services.outbox_service import (
    REFERENCE_PAYLOAD_FIELDS,
    OutboxService,
    SUPPORTED_OUTBOX_TOPICS,
)
from app.workers.jobs.outbox_delivery import OutboxDeliveryError, outbox_delivery_job
from app.workers.jobs import outbox_topics
from app.workers.jobs.recording_reconciliation import ReconciliationResult


async def _missing_handler(*_args, **_kwargs):
    pytest.fail("post-call outbox handler is not implemented")


deliver_recording_reconcile = getattr(
    outbox_topics,
    "deliver_recording_reconcile",
    _missing_handler,
)
deliver_summary_generate = getattr(
    outbox_topics,
    "deliver_summary_generate",
    _missing_handler,
)


class ExplodingNotificationProvider:
    async def send_notification(self, **_kwargs):
        raise AssertionError("Firebase push must remain disabled")


class TrackingSessionFactory:
    def __init__(self, base_factory) -> None:
        self.base_factory = base_factory
        self.open_contexts = 0

    @asynccontextmanager
    async def __call__(self):
        async with self.base_factory() as session:
            self.open_contexts += 1
            try:
                yield session
            finally:
                self.open_contexts -= 1


class FakeSummaryProvider:
    def __init__(self, factory: TrackingSessionFactory, *, fail=False) -> None:
        self.factory = factory
        self.fail = fail
        self.transcripts: list[list[dict]] = []

    async def generate_summary(self, transcript: list[dict]):
        assert self.factory.open_contexts == 0
        self.transcripts.append(transcript)
        if self.fail:
            raise ProviderFailure(
                provider="gemini",
                operation="generate_summary",
                disposition="retryable",
                error_class="unavailable",
            )
        return StructuredSummary(
            summary_text="A durable summary",
            caller_intent="Ask a question",
            action_items=["Reply"],
            sentiment="neutral",
            follow_up_required=True,
        )


class FakeRecordingReconciler:
    def __init__(self, result: ReconciliationResult) -> None:
        self.result = result
        self.calls = []

    async def reconcile(self, operation_id):
        self.calls.append(operation_id)
        return self.result


def event(*, call_id, topic: str, aggregate_type: str) -> OutboxEvent:
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


def recording_event(operation_id) -> OutboxEvent:
    return OutboxEvent(
        id=uuid4(),
        idempotency_key=f"recording.reconcile:{operation_id}:start",
        topic="recording.reconcile",
        aggregate_type="recording-egress-operation",
        aggregate_id=operation_id,
        payload={"operation_id": str(operation_id)},
        status="processing",
        attempt_count=1,
        next_attempt_at=datetime.now(UTC),
    )


@pytest.mark.anyio
async def test_summary_handler_snapshots_then_persists_in_fresh_transaction(
    db_session,
    active_user,
) -> None:
    call = Call(user_id=active_user.id, status="completed", duration_seconds=1)
    db_session.add(call)
    await db_session.flush()
    call_id = call.id
    db_session.add_all(
        [
            CallMessage(
                call_id=call.id,
                sequence_number=2,
                speaker="AGENT",
                text="Second",
            ),
            CallMessage(
                call_id=call.id,
                sequence_number=1,
                speaker="CALLER",
                text="First",
            ),
        ]
    )
    await db_session.commit()
    call_id = call.id
    factory = TrackingSessionFactory(
        async_sessionmaker(db_session.bind, expire_on_commit=False)
    )
    provider = FakeSummaryProvider(factory)

    await deliver_summary_generate(
        {
            "session_factory": factory,
            "summary_provider": provider,
            "notification_provider": ExplodingNotificationProvider(),
        },
        event(call_id=call_id, topic="summary.generate", aggregate_type="call-summary"),
    )

    assert provider.transcripts == [
        [
            {"speaker": "CALLER", "text": "First"},
            {"speaker": "AGENT", "text": "Second"},
        ]
    ]
    db_session.expire_all()
    stored = await db_session.get(Call, call_id)
    assert stored.summary_text == "A durable summary"
    assert stored.summary_data["follow_up_required"] is True


@pytest.mark.anyio
async def test_existing_summary_is_idempotent_without_provider_call(
    db_session,
    active_user,
) -> None:
    call = Call(
        user_id=active_user.id,
        status="completed",
        duration_seconds=1,
        summary_text="First persisted summary",
        summary_data={"summary_text": "First persisted summary"},
    )
    db_session.add(call)
    await db_session.commit()
    call_id = call.id
    factory = TrackingSessionFactory(
        async_sessionmaker(db_session.bind, expire_on_commit=False)
    )
    provider = FakeSummaryProvider(factory)

    await deliver_summary_generate(
        {"session_factory": factory, "summary_provider": provider},
        event(call_id=call_id, topic="summary.generate", aggregate_type="call-summary"),
    )

    assert provider.transcripts == []
    db_session.expire_all()
    stored = await db_session.get(Call, call_id)
    assert stored.summary_text == "First persisted summary"


@pytest.mark.anyio
async def test_existing_summary_regenerates_when_it_does_not_cover_current_transcript(
    db_session,
    active_user,
) -> None:
    call = Call(
        user_id=active_user.id,
        status="completed",
        duration_seconds=1,
        summary_text="Stale summary",
        summary_data={"summary_text": "Stale summary"},
    )
    call.summary_transcript_max_sequence = 0
    db_session.add(call)
    await db_session.flush()
    call_id = call.id
    db_session.add(
        CallMessage(
            call_id=call.id,
            sequence_number=1,
            speaker="CALLER",
            text="Late durable line",
        )
    )
    await db_session.commit()
    factory = TrackingSessionFactory(
        async_sessionmaker(db_session.bind, expire_on_commit=False)
    )
    provider = FakeSummaryProvider(factory)

    await deliver_summary_generate(
        {"session_factory": factory, "summary_provider": provider},
        event(call_id=call_id, topic="summary.generate", aggregate_type="call-summary"),
    )

    assert provider.transcripts == [
        [{"speaker": "CALLER", "text": "Late durable line"}]
    ]
    db_session.expire_all()
    stored = await db_session.get(Call, call_id)
    assert stored.summary_text == "A durable summary"
    assert stored.summary_transcript_max_sequence == 1


@pytest.mark.anyio
async def test_fresh_lock_race_preserves_first_persisted_summary(
    db_session,
    active_user,
) -> None:
    call = Call(user_id=active_user.id, status="completed", duration_seconds=1)
    db_session.add(call)
    await db_session.flush()
    db_session.add(
        CallMessage(
            call_id=call.id,
            sequence_number=1,
            speaker="CALLER",
            text="Race",
        )
    )
    await db_session.commit()
    call_id = call.id
    factory = TrackingSessionFactory(
        async_sessionmaker(db_session.bind, expire_on_commit=False)
    )

    class RacingProvider(FakeSummaryProvider):
        async def generate_summary(self, transcript):
            result = await super().generate_summary(transcript)
            async with self.factory.base_factory() as session:
                racing_call = await session.get(Call, call_id, with_for_update=True)
                racing_call.summary_text = "Winner from another worker"
                racing_call.summary_data = {"summary_text": "Winner from another worker"}
                racing_call.summary_transcript_max_sequence = 1
                await session.commit()
            return result

    await deliver_summary_generate(
        {
            "session_factory": factory,
            "summary_provider": RacingProvider(factory),
        },
        event(call_id=call_id, topic="summary.generate", aggregate_type="call-summary"),
    )

    db_session.expire_all()
    stored = await db_session.get(Call, call_id)
    assert stored.summary_text == "Winner from another worker"


@pytest.mark.anyio
async def test_summary_handler_retries_when_transcript_changes_during_provider_io(
    db_session,
    active_user,
) -> None:
    call = Call(user_id=active_user.id, status="completed", duration_seconds=1)
    db_session.add(call)
    await db_session.flush()
    db_session.add(
        CallMessage(
            call_id=call.id,
            sequence_number=1,
            speaker="CALLER",
            text="Snapshot line",
        )
    )
    await db_session.commit()
    call_id = call.id
    factory = TrackingSessionFactory(
        async_sessionmaker(db_session.bind, expire_on_commit=False)
    )

    class LateTranscriptProvider(FakeSummaryProvider):
        async def generate_summary(self, transcript):
            result = await super().generate_summary(transcript)
            async with self.factory.base_factory() as session:
                session.add(
                    CallMessage(
                        call_id=call_id,
                        sequence_number=2,
                        speaker="AGENT",
                        text="Arrived during provider I/O",
                    )
                )
                await session.commit()
            return result

    with pytest.raises(OutboxDeliveryError) as exc_info:
        await deliver_summary_generate(
            {
                "session_factory": factory,
                "summary_provider": LateTranscriptProvider(factory),
            },
            event(
                call_id=call_id,
                topic="summary.generate",
                aggregate_type="call-summary",
            ),
        )

    assert exc_info.value.retryable is True
    assert exc_info.value.error_code == "summary_stale"
    db_session.expire_all()
    stored = await db_session.get(Call, call_id)
    assert stored.summary_data is None


@pytest.mark.anyio
async def test_summary_handler_empty_transcript_is_successful_noop(
    db_session,
    active_user,
) -> None:
    call = Call(user_id=active_user.id, status="completed", duration_seconds=1)
    db_session.add(call)
    await db_session.commit()
    call_id = call.id
    factory = TrackingSessionFactory(
        async_sessionmaker(db_session.bind, expire_on_commit=False)
    )
    provider = FakeSummaryProvider(factory)

    await deliver_summary_generate(
        {"session_factory": factory, "summary_provider": provider},
        event(call_id=call_id, topic="summary.generate", aggregate_type="call-summary"),
    )

    assert provider.transcripts == []
    db_session.expire_all()
    stored = await db_session.get(Call, call_id)
    assert stored.summary_transcript_max_sequence == 0

    async with factory.base_factory() as session:
        session.add(
            CallMessage(
                call_id=call_id,
                sequence_number=1,
                speaker="CALLER",
                text="Recovered after empty delivery",
            )
        )
        await session.commit()

    await deliver_summary_generate(
        {"session_factory": factory, "summary_provider": provider},
        event(call_id=call_id, topic="summary.generate", aggregate_type="call-summary"),
    )
    assert provider.transcripts == [
        [{"speaker": "CALLER", "text": "Recovered after empty delivery"}]
    ]


@pytest.mark.anyio
async def test_summary_handler_rejects_deleted_call_without_recreating_metadata(
    db_session,
    active_user,
) -> None:
    call = Call(
        user_id=active_user.id,
        status="completed",
        duration_seconds=1,
        deleted_at=datetime.now(UTC),
    )
    db_session.add(call)
    await db_session.commit()
    call_id = call.id
    factory = TrackingSessionFactory(
        async_sessionmaker(db_session.bind, expire_on_commit=False)
    )

    with pytest.raises(OutboxDeliveryError) as exc_info:
        await deliver_summary_generate(
            {"session_factory": factory},
            event(
                call_id=call_id,
                topic="summary.generate",
                aggregate_type="call-summary",
            ),
        )

    assert exc_info.value.error_code == "provider_terminal"
    assert exc_info.value.retryable is False
    db_session.expire_all()
    stored = await db_session.get(Call, call_id)
    assert stored is not None
    assert stored.summary_text is None
    assert stored.summary_data is None
    assert stored.summary_transcript_max_sequence is None
    assert stored.recording_object_key is None
    assert stored.recording_url is None
    assert stored.recording_egress_id is None


@pytest.mark.anyio
async def test_summary_handler_provider_failure_is_retryable(
    db_session,
    active_user,
) -> None:
    call = Call(user_id=active_user.id, status="completed", duration_seconds=1)
    db_session.add(call)
    await db_session.flush()
    db_session.add(
        CallMessage(
            call_id=call.id,
            sequence_number=1,
            speaker="CALLER",
            text="First",
        )
    )
    await db_session.commit()
    factory = TrackingSessionFactory(
        async_sessionmaker(db_session.bind, expire_on_commit=False)
    )

    with pytest.raises(OutboxDeliveryError) as exc_info:
        await deliver_summary_generate(
            {
                "session_factory": factory,
                "summary_provider": FakeSummaryProvider(factory, fail=True),
            },
            event(
                call_id=call.id,
                topic="summary.generate",
                aggregate_type="call-summary",
            ),
        )

    assert exc_info.value.retryable is True


@pytest.mark.anyio
async def test_summary_handler_marks_malformed_provider_summary_terminal(
    db_session,
    active_user,
) -> None:
    call = Call(user_id=active_user.id, status="completed", duration_seconds=1)
    db_session.add(call)
    await db_session.flush()
    db_session.add(
        CallMessage(
            call_id=call.id,
            sequence_number=1,
            speaker="CALLER",
            text="First",
        )
    )
    await db_session.commit()
    factory = TrackingSessionFactory(
        async_sessionmaker(db_session.bind, expire_on_commit=False)
    )

    class MalformedSummaryProvider:
        async def generate_summary(self, transcript: list[dict]):
            return {"summary_text": "missing schema"}

    with pytest.raises(OutboxDeliveryError) as exc_info:
        await deliver_summary_generate(
            {"session_factory": factory, "summary_provider": MalformedSummaryProvider()},
            event(
                call_id=call.id,
                topic="summary.generate",
                aggregate_type="call-summary",
            ),
        )

    assert exc_info.value.error_code == "provider_terminal"
    assert exc_info.value.retryable is False


@pytest.mark.anyio
async def test_summary_handler_propagates_injected_defects(
    db_session,
    active_user,
) -> None:
    call = Call(user_id=active_user.id, status="completed", duration_seconds=1)
    db_session.add(call)
    await db_session.flush()
    db_session.add(
        CallMessage(
            call_id=call.id,
            sequence_number=1,
            speaker="CALLER",
            text="First",
        )
    )
    await db_session.commit()
    factory = TrackingSessionFactory(
        async_sessionmaker(db_session.bind, expire_on_commit=False)
    )

    class DefectiveSummaryProvider:
        async def generate_summary(self, transcript: list[dict]):
            raise RuntimeError("SUMMARY_DEFECT_SENTINEL")

    with pytest.raises(RuntimeError, match="SUMMARY_DEFECT_SENTINEL"):
        await deliver_summary_generate(
            {"session_factory": factory, "summary_provider": DefectiveSummaryProvider()},
            event(
                call_id=call.id,
                topic="summary.generate",
                aggregate_type="call-summary",
            ),
        )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("handler", "topic", "aggregate_type"),
    [
        (deliver_summary_generate, "summary.generate", "call-summary"),
        (
            deliver_recording_reconcile,
            "recording.reconcile",
            "recording-egress-operation",
        ),
    ],
)
async def test_post_call_handlers_validate_exact_aggregate_identity(
    handler,
    topic: str,
    aggregate_type: str,
) -> None:
    call_id = uuid4()
    malformed = (
        recording_event(call_id)
        if topic == "recording.reconcile"
        else event(
            call_id=call_id,
            topic=topic,
            aggregate_type=aggregate_type,
        )
    )
    malformed.aggregate_type = "call"

    with pytest.raises(OutboxDeliveryError) as exc_info:
        await handler({}, malformed)

    assert exc_info.value.retryable is False
    assert exc_info.value.error_code == "invalid_payload"


@pytest.mark.anyio
async def test_recording_reconcile_validates_reference_before_building_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operation_id = uuid4()
    malformed = recording_event(operation_id)
    malformed.payload = {"operation_id": str(uuid4())}
    built = False

    def build(_ctx):
        nonlocal built
        built = True
        raise AssertionError("builder must not run for invalid input")

    monkeypatch.setattr(outbox_topics, "build_recording_reconciler", build)

    with pytest.raises(OutboxDeliveryError) as exc_info:
        await deliver_recording_reconcile({}, malformed)

    assert exc_info.value.error_code == "invalid_payload"
    assert exc_info.value.retryable is False
    assert built is False


@pytest.mark.anyio
async def test_recording_reconcile_invokes_reconciler_with_operation_identity() -> None:
    operation_id = uuid4()
    reconciler = FakeRecordingReconciler(ReconciliationResult("complete"))

    await deliver_recording_reconcile(
        {"recording_reconciler": reconciler},
        recording_event(operation_id),
    )

    assert reconciler.calls == [operation_id]


@pytest.mark.anyio
async def test_recording_reconcile_retry_is_bounded_and_non_exhausting() -> None:
    operation_id = uuid4()
    reconciler = FakeRecordingReconciler(
        ReconciliationResult("retry", "recording_identity_mismatch")
    )

    with pytest.raises(OutboxDeliveryError) as exc_info:
        await deliver_recording_reconcile(
            {"recording_reconciler": reconciler},
            recording_event(operation_id),
        )

    assert exc_info.value.error_code == "recording_identity_mismatch"
    assert exc_info.value.retryable is True
    assert exc_info.value.exhaustible is False


@pytest.mark.anyio
@pytest.mark.parametrize("failure_site", ["builder", "reconciler"])
async def test_recording_reconcile_unexpected_failures_are_non_exhausting(
    monkeypatch: pytest.MonkeyPatch,
    failure_site: str,
) -> None:
    operation_id = uuid4()

    class ExplodingReconciler:
        async def reconcile(self, _operation_id):
            raise RuntimeError("DATABASE_CREDENTIAL_SENTINEL")

    if failure_site == "builder":
        def explode(_ctx):
            raise RuntimeError("BUILDER_CREDENTIAL_SENTINEL")

        monkeypatch.setattr(outbox_topics, "build_recording_reconciler", explode)
        ctx = {}
    else:
        ctx = {"recording_reconciler": ExplodingReconciler()}

    with pytest.raises(OutboxDeliveryError) as exc_info:
        await deliver_recording_reconcile(ctx, recording_event(operation_id))

    assert exc_info.value.error_code == "recording_unresolved"
    assert exc_info.value.retryable is True
    assert exc_info.value.exhaustible is False
    assert "SENTINEL" not in str(exc_info.value)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "malformed_result",
    [
        ReconciliationResult(
            "retry",
            cast(Any, "provider_retryable"),
        ),
        SimpleNamespace(
            outcome="complete",
            error_code="recording_unresolved",
        ),
        SimpleNamespace(outcome="unexpected", error_code=None),
        SimpleNamespace(error_code=None),
    ],
)
async def test_recording_reconcile_malformed_results_fail_closed_without_exhaustion(
    malformed_result,
) -> None:
    operation_id = uuid4()

    with pytest.raises(OutboxDeliveryError) as exc_info:
        await deliver_recording_reconcile(
            {"recording_reconciler": FakeRecordingReconciler(malformed_result)},
            recording_event(operation_id),
        )

    assert exc_info.value.error_code == "recording_unresolved"
    assert exc_info.value.retryable is True
    assert exc_info.value.exhaustible is False


def test_default_handlers_exactly_match_supported_topics_without_placeholders() -> None:
    expected_topics = {
        "account.deactivate",
        "phone.provision",
        "provider.cleanup",
        "phone.enable",
        "phone.disable",
        "livekit.dispatch",
        "livekit.verification_dispatch",
        "summary.generate",
        "recording.reconcile",
    }

    assert set(SUPPORTED_OUTBOX_TOPICS) == expected_topics
    assert set(REFERENCE_PAYLOAD_FIELDS) == expected_topics
    assert set(outbox_topics.DEFAULT_OUTBOX_HANDLERS) == expected_topics


@pytest.mark.anyio
async def test_recording_reconcile_holding_handler_remains_pending_after_exhaustion(
    db_session,
) -> None:
    operation_id = uuid4()
    now = datetime(2026, 7, 19, tzinfo=UTC)
    event = await OutboxService(db_session).add(
        topic="recording.reconcile",
        aggregate_type="recording-egress-operation",
        aggregate_id=operation_id,
        idempotency_key=f"recording.reconcile:{operation_id}:start",
        payload={"operation_id": str(operation_id)},
        next_attempt_at=now,
    )
    event.attempt_count = 5
    event_id = event.id
    await db_session.commit()
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    result = await outbox_delivery_job(
        {
            "session_factory": factory,
            "recording_reconciler": FakeRecordingReconciler(
                ReconciliationResult("retry", "recording_unresolved")
            ),
            "outbox_handlers": {
                "recording.reconcile": deliver_recording_reconcile,
            },
            "outbox_now": lambda: now,
        }
    )

    assert result == {"claimed": 1, "delivered": 0, "retried": 1, "failed": 0}
    db_session.expire_all()
    stored = await db_session.scalar(
        select(OutboxEvent).where(OutboxEvent.id == event_id)
    )
    assert stored is not None
    assert stored.status == "pending"
    assert stored.attempt_count == 6
    assert stored.last_error_code == "recording_unresolved"
    assert stored.next_attempt_at.replace(tzinfo=UTC) == now + timedelta(hours=2)


@pytest.mark.anyio
async def test_retrying_summary_does_not_block_recording_operation_aggregate(
    db_session,
    active_user,
) -> None:
    call = Call(user_id=active_user.id, status="completed", duration_seconds=1)
    db_session.add(call)
    await db_session.flush()
    outbox = OutboxService(db_session)
    await outbox.add(
        topic="summary.generate",
        aggregate_type="call-summary",
        aggregate_id=call.id,
        idempotency_key=f"summary.generate:{call.id}",
        payload={"call_id": str(call.id)},
    )
    await outbox.add(
        topic="recording.reconcile",
        aggregate_type="recording-egress-operation",
        aggregate_id=(operation_id := uuid4()),
        idempotency_key=f"recording.reconcile:{operation_id}:start",
        payload={"operation_id": str(operation_id)},
    )
    await db_session.commit()
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    delivered: list[str] = []

    async def failing_summary(_ctx, _event):
        raise OutboxDeliveryError("provider_retryable", retryable=True)

    async def successful_recording(_ctx, _event):
        delivered.append("recording.reconcile")

    result = await outbox_delivery_job(
        {
            "session_factory": factory,
            "outbox_handlers": {
                "summary.generate": failing_summary,
                "recording.reconcile": successful_recording,
            },
            "outbox_now": lambda: datetime.now(UTC),
        }
    )

    assert result == {"claimed": 2, "delivered": 1, "retried": 1, "failed": 0}
    assert delivered == ["recording.reconcile"]
