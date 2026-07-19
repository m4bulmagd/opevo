import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from redis.asyncio import Redis
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.models import Base
from app.models.call import Call
from app.models.outbox_event import OutboxEvent
from app.models.recording_egress_operation import RecordingEgressOperation
from app.models.usage_ledger import UsageLedger
from app.models.user import User
from app.models.webhook_event import WebhookEvent
from app.providers.livekit_recording.base import (
    RecordingEgressResult,
    RecordingEgressSnapshot,
)
from app.repositories.outbox_repository import OutboxRepository
from app.repositories.webhook_event_repository import WebhookEventRepository
from app.services.call_history_service import CallHistoryService
from app.services.call_lifecycle_service import CallLifecycleService
from app.services.recording_lifecycle_service import (
    RecordingEgressEventFact,
    RecordingLifecycleService,
    RecordingStartClaim,
)
from app.workers.jobs.outbox_delivery import OutboxDeliveryError
from app.workers.jobs.outbox_topics import deliver_recording_reconcile
from app.workers.jobs.recording_reconciliation import RecordingReconciler


FIXED_NOW = datetime(2026, 7, 20, 10, 0, tzinfo=UTC)
STOP_NOW = FIXED_NOW + timedelta(minutes=1)
DELETION_NOW = FIXED_NOW + timedelta(minutes=2)


@dataclass(frozen=True)
class _RecordingSeed:
    user_id: UUID
    call_id: UUID
    operation_id: UUID
    expected_object_key: str


class _BlockingRecordingProvider:
    def __init__(self, *, egress_id: str) -> None:
        self.egress_id = egress_id
        self.entered = asyncio.Event()
        self.resume = asyncio.Event()
        self.starts: list[dict[str, str]] = []

    async def start_room_recording(
        self,
        *,
        room_name: str,
        object_key: str,
    ) -> RecordingEgressResult:
        self.starts.append({"room_name": room_name, "object_key": object_key})
        self.entered.set()
        await asyncio.wait_for(self.resume.wait(), timeout=2)
        return RecordingEgressResult(
            egress_id=self.egress_id,
            object_key=object_key,
            url="s3://synthetic-task7/recording.ogg",
        )


class _BlockingListingProvider:
    def __init__(self, *, snapshot: RecordingEgressSnapshot) -> None:
        self.snapshot = snapshot
        self.entered = asyncio.Event()
        self.resume = asyncio.Event()
        self.listed_rooms: list[str] = []
        self.stopped_ids: list[str] = []

    async def list_room_egresses(
        self,
        *,
        room_name: str,
    ) -> tuple[RecordingEgressSnapshot, ...]:
        self.listed_rooms.append(room_name)
        self.entered.set()
        await asyncio.wait_for(self.resume.wait(), timeout=2)
        return (self.snapshot,)

    async def ensure_not_running(self, egress_id: str) -> None:
        self.stopped_ids.append(egress_id)


class _NoListingProvider:
    async def list_room_egresses(
        self,
        *,
        room_name: str,
    ) -> tuple[RecordingEgressSnapshot, ...]:
        pytest.fail(f"definitive non-start must not list room {room_name}")

    async def ensure_not_running(self, egress_id: str) -> None:
        pytest.fail(f"definitive non-start must not stop egress {egress_id}")


class _ExactListingProvider:
    def __init__(
        self,
        snapshots: tuple[RecordingEgressSnapshot, ...],
        *,
        stop_failures: frozenset[str] = frozenset(),
    ) -> None:
        self.snapshots = snapshots
        self.stop_failures = stop_failures
        self.listed_rooms: list[str] = []
        self.stop_attempts: list[str] = []

    async def list_room_egresses(
        self,
        *,
        room_name: str,
    ) -> tuple[RecordingEgressSnapshot, ...]:
        self.listed_rooms.append(room_name)
        return self.snapshots

    async def ensure_not_running(self, egress_id: str) -> None:
        self.stop_attempts.append(egress_id)
        if egress_id in self.stop_failures:
            raise RuntimeError("synthetic task7 stop failure")


class _RecordingSignalObservability:
    def __init__(self) -> None:
        self.results: list[str] = []
        self.multiple_exact_count = 0

    def record_recording_reconciliation_result(self, result: str) -> None:
        self.results.append(result)

    def record_multiple_exact_match_conflict(self) -> None:
        self.multiple_exact_count += 1


class _RecordingStorage:
    def __init__(self) -> None:
        self.deleted_keys: list[str] = []

    async def delete_object(self, *, object_key: str) -> None:
        self.deleted_keys.append(object_key)


class _PausingBeforeClaimLifecycle(RecordingLifecycleService):
    def __init__(
        self,
        session: AsyncSession,
        *,
        entered: asyncio.Event,
        resume: asyncio.Event,
    ) -> None:
        super().__init__(session, now_provider=lambda: FIXED_NOW)
        self.entered = entered
        self.resume = resume

    async def begin_start(self, operation_id: UUID) -> RecordingStartClaim | None:
        self.entered.set()
        await asyncio.wait_for(self.resume.wait(), timeout=2)
        return await super().begin_start(operation_id)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


async def _seed_prepared_operation(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    suffix: str,
    with_usage_credit: bool = False,
) -> _RecordingSeed:
    async with session_factory() as session:
        user = User(
            clerk_user_id=f"task7-{suffix}-user",
            email=f"task7-{suffix}@example.invalid",
        )
        session.add(user)
        await session.flush()
        call = Call(
            user_id=user.id,
            livekit_room_id=f"task7-room-{suffix}",
            caller_number="+33900000000",
            status="connected",
            started_at=FIXED_NOW,
            summary_text="synthetic task7 summary",
            summary_data={"caller_intent": "synthetic"},
            summary_transcript_max_sequence=1,
        )
        session.add(call)
        if with_usage_credit:
            session.add(
                UsageLedger(
                    user_id=user.id,
                    event_type="subscription_activated",
                    source_id=f"task7-credit-{suffix}",
                    minutes_delta=5,
                    balance_after=5,
                )
            )
        await session.flush()
        operation = await RecordingLifecycleService(
            session,
            now_provider=lambda: FIXED_NOW,
        ).prepare_start(call)
        await session.commit()
        return _RecordingSeed(
            user_id=user.id,
            call_id=call.id,
            operation_id=operation.id,
            expected_object_key=operation.expected_object_key,
        )


async def _run_recording_start(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    operation_id: UUID,
    provider: _BlockingRecordingProvider,
    before_claim_entered: asyncio.Event | None = None,
    before_claim_resume: asyncio.Event | None = None,
) -> RecordingStartClaim | None:
    async with session_factory() as claim_session:
        lifecycle: RecordingLifecycleService
        if before_claim_entered is not None and before_claim_resume is not None:
            lifecycle = _PausingBeforeClaimLifecycle(
                claim_session,
                entered=before_claim_entered,
                resume=before_claim_resume,
            )
        else:
            lifecycle = RecordingLifecycleService(
                claim_session,
                now_provider=lambda: FIXED_NOW,
            )
        claim = await lifecycle.begin_start(operation_id)
        await claim_session.commit()
    if claim is None:
        return None

    result = await provider.start_room_recording(
        room_name=claim.room_name,
        object_key=claim.expected_object_key,
    )
    async with session_factory() as result_session:
        await RecordingLifecycleService(
            result_session,
            now_provider=lambda: STOP_NOW,
        ).record_start_success(claim.operation_id, result)
        await result_session.commit()
    return claim


async def _seed_terminal_starting_operation(
    session_factory: async_sessionmaker[AsyncSession],
) -> _RecordingSeed:
    seed = await _seed_prepared_operation(
        session_factory,
        suffix="simultaneous-success-delete",
    )
    async with session_factory() as session:
        claim = await RecordingLifecycleService(
            session,
            now_provider=lambda: FIXED_NOW,
        ).begin_start(seed.operation_id)
        assert claim is not None
        call = await session.get(Call, seed.call_id)
        assert call is not None
        call.status = "completed"
        call.ended_at = STOP_NOW
        call.duration_seconds = 60
        call.minutes_charged = 1
        await session.commit()
    return seed


async def _seed_started_terminal_operation(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    suffix: str,
) -> _RecordingSeed:
    seed = await _seed_prepared_operation(session_factory, suffix=suffix)
    async with session_factory() as session:
        lifecycle = RecordingLifecycleService(
            session,
            now_provider=lambda: FIXED_NOW,
        )
        claim = await lifecycle.begin_start(seed.operation_id)
        assert claim is not None
        call = await session.get(Call, seed.call_id)
        assert call is not None
        call.status = "completed"
        call.ended_at = STOP_NOW
        call.duration_seconds = 60
        call.minutes_charged = 1
        recorded = await lifecycle.record_start_success(
            seed.operation_id,
            RecordingEgressResult(
                egress_id=f"task7-egress-{suffix}",
                object_key=seed.expected_object_key,
                url=f"s3://synthetic-task7/{suffix}.ogg",
            ),
        )
        assert recorded is not None
        await session.commit()
    return seed


async def _release_and_cancel(
    task: asyncio.Task[object],
    *release_events: asyncio.Event,
) -> None:
    for event in release_events:
        event.set()
    if not task.done():
        task.cancel()
    with suppress(TimeoutError):
        await asyncio.wait_for(
            asyncio.gather(task, return_exceptions=True),
            timeout=2,
        )


@pytest_asyncio.fixture
async def recording_session_factory() -> AsyncIterator[
    async_sessionmaker[AsyncSession]
]:
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("PostgreSQL recording concurrency test requires TEST_DATABASE_URL")
    if database_url.startswith("postgresql://"):
        database_url = database_url.replace(
            "postgresql://",
            "postgresql+asyncpg://",
            1,
        )
    if not database_url.startswith("postgresql+asyncpg://"):
        pytest.skip("TEST_DATABASE_URL must identify PostgreSQL")

    schema_name = f"task7_recording_{uuid4().hex}"
    quoted_schema = f'"{schema_name}"'
    admin_engine = create_async_engine(database_url, isolation_level="AUTOCOMMIT")
    schema_engine = None
    try:
        async with admin_engine.connect() as connection:
            await connection.execute(text(f"CREATE SCHEMA {quoted_schema}"))
        schema_engine = create_async_engine(
            database_url,
            connect_args={"server_settings": {"search_path": schema_name}},
        )
        async with schema_engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        yield async_sessionmaker(schema_engine, expire_on_commit=False)
    finally:
        if schema_engine is not None:
            await schema_engine.dispose()
        async with admin_engine.connect() as connection:
            await connection.execute(
                text(f"DROP SCHEMA IF EXISTS {quoted_schema} CASCADE")
            )
        await admin_engine.dispose()


@pytest.mark.anyio
async def test_task7_redis_readiness_dependency_responds() -> None:
    redis_url = os.getenv("TEST_REDIS_URL")
    if not redis_url:
        pytest.skip("Recording concurrency test requires TEST_REDIS_URL")

    client = Redis.from_url(redis_url, decode_responses=True)
    try:
        assert await asyncio.wait_for(client.ping(), timeout=2) is True
    finally:
        await client.aclose()


@pytest.mark.anyio
async def test_schedule_1_end_before_start_claim_prevents_provider_io(
    recording_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    seed = await _seed_prepared_operation(
        recording_session_factory,
        suffix="end-before-claim",
    )
    before_claim_entered = asyncio.Event()
    before_claim_resume = asyncio.Event()
    provider = _BlockingRecordingProvider(egress_id="task7-egress-never-started")
    start_task = asyncio.create_task(
        _run_recording_start(
            recording_session_factory,
            operation_id=seed.operation_id,
            provider=provider,
            before_claim_entered=before_claim_entered,
            before_claim_resume=before_claim_resume,
        )
    )

    first_stop_requested_at: datetime
    try:
        await asyncio.wait_for(before_claim_entered.wait(), timeout=2)
        async with recording_session_factory() as end_session:
            lifecycle = RecordingLifecycleService(
                end_session,
                now_provider=lambda: STOP_NOW,
            )
            await CallLifecycleService(
                end_session,
                recording_lifecycle_service=lifecycle,
            ).end_from_agent(
                call_id=seed.call_id,
                duration_seconds=60,
                ended_at=STOP_NOW,
            )
            operation_after_end = await end_session.get(
                RecordingEgressOperation,
                seed.operation_id,
            )
            assert operation_after_end is not None
            assert operation_after_end.stop_requested_at is not None
            first_stop_requested_at = operation_after_end.stop_requested_at
            await end_session.commit()

        before_claim_resume.set()
        claim = await asyncio.wait_for(start_task, timeout=2)
    finally:
        await _release_and_cancel(start_task, before_claim_resume, provider.resume)

    assert claim is None
    assert provider.starts == []
    async with recording_session_factory() as session:
        call = await session.get(Call, seed.call_id)
        operation = await session.get(RecordingEgressOperation, seed.operation_id)
        events = list(
            (
                await session.scalars(
                    select(OutboxEvent).where(
                        OutboxEvent.aggregate_type == "recording-egress-operation",
                        OutboxEvent.aggregate_id == seed.operation_id,
                    )
                )
            ).all()
        )

    assert call is not None
    assert call.status == "ending"
    assert operation is not None
    assert operation.start_state == "prepared"
    assert operation.provider_egress_id is None
    assert _as_utc(operation.stop_requested_at) == _as_utc(first_stop_requested_at)
    assert _as_utc(operation.stop_requested_at) == STOP_NOW
    assert {event.idempotency_key for event in events} == {
        f"recording.reconcile:{seed.operation_id}:start",
        f"recording.reconcile:{seed.operation_id}:stop",
    }
    assert all(event.status == "pending" for event in events)
    assert all(
        event.payload == {"operation_id": str(seed.operation_id)} for event in events
    )

    async with recording_session_factory() as retry_session:
        second_claim = await RecordingLifecycleService(
            retry_session,
            now_provider=lambda: STOP_NOW + timedelta(minutes=5),
        ).begin_start(seed.operation_id)
        await retry_session.commit()

    assert second_claim is None
    assert provider.starts == []


@pytest.mark.anyio
async def test_schedule_2_end_after_start_claim_preserves_late_success_and_stop(
    recording_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    seed = await _seed_prepared_operation(
        recording_session_factory,
        suffix="end-after-claim",
    )
    provider = _BlockingRecordingProvider(egress_id="task7-egress-end-after-claim")
    start_task = asyncio.create_task(
        _run_recording_start(
            recording_session_factory,
            operation_id=seed.operation_id,
            provider=provider,
        )
    )

    try:
        await asyncio.wait_for(provider.entered.wait(), timeout=2)
        async with recording_session_factory() as end_session:
            await CallLifecycleService(
                end_session,
                recording_lifecycle_service=RecordingLifecycleService(
                    end_session,
                    now_provider=lambda: STOP_NOW,
                ),
            ).end_from_agent(
                call_id=seed.call_id,
                duration_seconds=60,
                ended_at=STOP_NOW,
            )
            await end_session.commit()

        provider.resume.set()
        claim = await asyncio.wait_for(start_task, timeout=2)
    finally:
        await _release_and_cancel(start_task, provider.resume)

    assert claim is not None
    assert len(provider.starts) == 1
    assert provider.starts[0] == {
        "room_name": "task7-room-end-after-claim",
        "object_key": seed.expected_object_key,
    }
    async with recording_session_factory() as session:
        call = await session.get(Call, seed.call_id)
        operation = await session.get(RecordingEgressOperation, seed.operation_id)
        events = list(
            (
                await session.scalars(
                    select(OutboxEvent).where(
                        OutboxEvent.aggregate_type == "recording-egress-operation",
                        OutboxEvent.aggregate_id == seed.operation_id,
                    )
                )
            ).all()
        )

    assert call is not None
    assert call.status == "ending"
    assert call.recording_object_key == seed.expected_object_key
    assert call.recording_egress_id == "task7-egress-end-after-claim"
    assert operation is not None
    assert operation.start_state == "started"
    assert operation.provider_egress_id == "task7-egress-end-after-claim"
    assert operation.stop_requested_at is not None
    assert _as_utc(operation.stop_requested_at) == STOP_NOW
    stop_event = next(
        event
        for event in events
        if event.idempotency_key == f"recording.reconcile:{seed.operation_id}:stop"
    )
    assert stop_event.status == "pending"
    assert _as_utc(stop_event.next_attempt_at) <= STOP_NOW
    assert stop_event.payload == {"operation_id": str(seed.operation_id)}

    async with recording_session_factory() as retry_session:
        second_claim = await RecordingLifecycleService(
            retry_session,
            now_provider=lambda: STOP_NOW + timedelta(minutes=5),
        ).begin_start(seed.operation_id)
        await retry_session.commit()

    assert second_claim is None
    assert len(provider.starts) == 1


@pytest.mark.anyio
async def test_schedule_3_owner_deletion_during_start_keeps_identity_private(
    recording_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    seed = await _seed_prepared_operation(
        recording_session_factory,
        suffix="delete-in-flight",
        with_usage_credit=True,
    )
    provider = _BlockingRecordingProvider(egress_id="task7-egress-delete-in-flight")
    start_task = asyncio.create_task(
        _run_recording_start(
            recording_session_factory,
            operation_id=seed.operation_id,
            provider=provider,
        )
    )

    stop_before_success: datetime
    delete_before_success: datetime
    try:
        await asyncio.wait_for(provider.entered.wait(), timeout=2)
        async with recording_session_factory() as delete_session:
            call_lifecycle = CallLifecycleService(
                delete_session,
                recording_lifecycle_service=RecordingLifecycleService(
                    delete_session,
                    now_provider=lambda: STOP_NOW,
                ),
            )
            await call_lifecycle.end_from_agent(
                call_id=seed.call_id,
                duration_seconds=60,
                ended_at=STOP_NOW,
            )
            await delete_session.commit()
            claim = await call_lifecycle.claim_finalization(seed.call_id)
            await call_lifecycle.complete_finalization(
                seed.call_id,
                generation=claim.generation,
            )
            await CallHistoryService(
                delete_session,
                recording_service=None,
                recording_lifecycle_service=RecordingLifecycleService(
                    delete_session,
                    now_provider=lambda: DELETION_NOW,
                ),
            ).delete_call(seed.user_id, seed.call_id)

        assert not start_task.done()
        async with recording_session_factory() as before_success_session:
            deleted_call = await before_success_session.get(Call, seed.call_id)
            in_flight_operation = await before_success_session.get(
                RecordingEgressOperation,
                seed.operation_id,
            )
        assert deleted_call is not None
        assert deleted_call.status == "completed"
        assert deleted_call.deleted_at is not None
        assert deleted_call.caller_number is None
        assert deleted_call.summary_text is None
        assert deleted_call.summary_data is None
        assert deleted_call.summary_transcript_max_sequence is None
        assert deleted_call.recording_object_key is None
        assert deleted_call.recording_egress_id is None
        assert deleted_call.recording_url is None
        assert in_flight_operation is not None
        assert in_flight_operation.start_state == "starting"
        assert in_flight_operation.provider_egress_id is None
        assert in_flight_operation.stop_requested_at is not None
        assert in_flight_operation.delete_requested_at is not None
        stop_before_success = in_flight_operation.stop_requested_at
        delete_before_success = in_flight_operation.delete_requested_at

        provider.resume.set()
        start_claim = await asyncio.wait_for(start_task, timeout=2)
    finally:
        await _release_and_cancel(start_task, provider.resume)

    assert start_claim is not None
    assert len(provider.starts) == 1
    async with recording_session_factory() as session:
        call = await session.get(Call, seed.call_id)
        operation = await session.get(RecordingEgressOperation, seed.operation_id)
        events = list(
            (
                await session.scalars(
                    select(OutboxEvent).where(
                        OutboxEvent.aggregate_type == "recording-egress-operation",
                        OutboxEvent.aggregate_id == seed.operation_id,
                    )
                )
            ).all()
        )

    assert call is not None
    assert call.deleted_at is not None
    assert call.caller_number is None
    assert call.summary_text is None
    assert call.summary_data is None
    assert call.summary_transcript_max_sequence is None
    assert call.recording_object_key is None
    assert call.recording_egress_id is None
    assert call.recording_url is None
    assert operation is not None
    assert operation.start_state == "started"
    assert operation.provider_egress_id == "task7-egress-delete-in-flight"
    assert operation.expected_object_key == seed.expected_object_key
    assert _as_utc(operation.stop_requested_at) == _as_utc(stop_before_success)
    assert _as_utc(operation.delete_requested_at) == _as_utc(delete_before_success)
    assert any(event.status in {"pending", "processing"} for event in events)
    assert all(
        event.payload == {"operation_id": str(seed.operation_id)} for event in events
    )


@pytest.mark.anyio
async def test_schedule_4_simultaneous_start_success_and_deletion_stays_tombstoned(
    recording_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    seed = await _seed_terminal_starting_operation(recording_session_factory)
    success_ready = asyncio.Event()
    deletion_ready = asyncio.Event()
    release = asyncio.Event()

    async def persist_success() -> None:
        async with recording_session_factory() as session:
            success_ready.set()
            await asyncio.wait_for(release.wait(), timeout=2)
            result = await RecordingLifecycleService(
                session,
                now_provider=lambda: DELETION_NOW,
            ).record_start_success(
                seed.operation_id,
                RecordingEgressResult(
                    egress_id="task7-egress-simultaneous-delete",
                    object_key=seed.expected_object_key,
                    url="s3://synthetic-task7/simultaneous.ogg",
                ),
            )
            assert result is not None
            await session.commit()

    async def delete_owner_call() -> None:
        async with recording_session_factory() as session:
            deletion_ready.set()
            await asyncio.wait_for(release.wait(), timeout=2)
            await CallHistoryService(
                session,
                recording_service=None,
                recording_lifecycle_service=RecordingLifecycleService(
                    session,
                    now_provider=lambda: DELETION_NOW,
                ),
            ).delete_call(seed.user_id, seed.call_id)

    success_task = asyncio.create_task(persist_success())
    deletion_task = asyncio.create_task(delete_owner_call())
    try:
        await asyncio.wait_for(success_ready.wait(), timeout=2)
        await asyncio.wait_for(deletion_ready.wait(), timeout=2)
        release.set()
        await asyncio.wait_for(
            asyncio.gather(success_task, deletion_task),
            timeout=2,
        )
    finally:
        await _release_and_cancel(success_task, release)
        await _release_and_cancel(deletion_task, release)

    async with recording_session_factory() as session:
        call = await session.get(Call, seed.call_id)
        operation = await session.get(RecordingEgressOperation, seed.operation_id)
        events = list(
            (
                await session.scalars(
                    select(OutboxEvent).where(
                        OutboxEvent.aggregate_type == "recording-egress-operation",
                        OutboxEvent.aggregate_id == seed.operation_id,
                    )
                )
            ).all()
        )

    assert call is not None
    assert call.status == "completed"
    assert call.deleted_at is not None
    assert call.caller_number is None
    assert call.summary_text is None
    assert call.summary_data is None
    assert call.summary_transcript_max_sequence is None
    assert call.recording_object_key is None
    assert call.recording_egress_id is None
    assert call.recording_url is None
    assert operation is not None
    assert operation.call_id == seed.call_id
    assert operation.room_name == "task7-room-simultaneous-success-delete"
    assert operation.expected_object_key == seed.expected_object_key
    assert operation.start_state == "started"
    assert operation.provider_egress_id == "task7-egress-simultaneous-delete"
    assert operation.stop_requested_at is not None
    assert operation.delete_requested_at is not None
    assert _as_utc(operation.stop_requested_at) == DELETION_NOW
    assert _as_utc(operation.delete_requested_at) == DELETION_NOW
    assert any(
        event.idempotency_key == f"recording.reconcile:{seed.operation_id}:delete"
        and event.status in {"pending", "processing"}
        for event in events
    )
    assert all(
        event.payload == {"operation_id": str(seed.operation_id)} for event in events
    )


@pytest.mark.anyio
async def test_schedule_5_duplicate_signed_fact_and_direct_success_converge(
    recording_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    seed = await _seed_terminal_starting_operation(recording_session_factory)
    direct_ready = asyncio.Event()
    webhook_ready = asyncio.Event()
    release = asyncio.Event()
    external_event_id = "task7-schedule-5-egress-started"
    egress_id = "task7-egress-direct-and-signed"

    async def persist_direct_success() -> None:
        async with recording_session_factory() as session:
            direct_ready.set()
            await asyncio.wait_for(release.wait(), timeout=2)
            recorded = await RecordingLifecycleService(
                session,
                now_provider=lambda: DELETION_NOW,
            ).record_start_success(
                seed.operation_id,
                RecordingEgressResult(
                    egress_id=egress_id,
                    object_key=seed.expected_object_key,
                    url="s3://synthetic-task7/direct-and-signed.ogg",
                ),
            )
            assert recorded is not None
            await session.commit()

    async def persist_signed_fact() -> None:
        async with recording_session_factory() as session:
            webhook_ready.set()
            await asyncio.wait_for(release.wait(), timeout=2)
            is_new = await WebhookEventRepository(session).record_if_new(
                provider="livekit",
                external_event_id=external_event_id,
                event_type="egress_started",
                payload={},
            )
            assert is_new is True
            outcome = await RecordingLifecycleService(
                session,
                now_provider=lambda: DELETION_NOW,
            ).accept_egress_event(
                RecordingEgressEventFact(
                    external_event_id=external_event_id,
                    event_type="egress_started",
                    egress_id=egress_id,
                    room_name="task7-room-simultaneous-success-delete",
                    status=1,
                    object_key=seed.expected_object_key,
                    object_key_evidence="exact",
                )
            )
            assert outcome == "accepted"
            await session.commit()

    direct_task = asyncio.create_task(persist_direct_success())
    webhook_task = asyncio.create_task(persist_signed_fact())
    try:
        await asyncio.wait_for(direct_ready.wait(), timeout=2)
        await asyncio.wait_for(webhook_ready.wait(), timeout=2)
        release.set()
        await asyncio.wait_for(
            asyncio.gather(direct_task, webhook_task),
            timeout=2,
        )
    finally:
        await _release_and_cancel(direct_task, release)
        await _release_and_cancel(webhook_task, release)

    async with recording_session_factory() as duplicate_session:
        repeated_is_new = await WebhookEventRepository(duplicate_session).record_if_new(
            provider="livekit",
            external_event_id=external_event_id,
            event_type="egress_started",
            payload={},
        )
        await duplicate_session.commit()
    assert repeated_is_new is False

    async with recording_session_factory() as session:
        operation = await session.get(RecordingEgressOperation, seed.operation_id)
        call = await session.get(Call, seed.call_id)
        operation_count = await session.scalar(
            select(func.count())
            .select_from(RecordingEgressOperation)
            .where(RecordingEgressOperation.call_id == seed.call_id)
        )
        webhook_rows = list(
            (
                await session.scalars(
                    select(WebhookEvent).where(
                        WebhookEvent.provider == "livekit",
                        WebhookEvent.external_event_id == external_event_id,
                    )
                )
            ).all()
        )
        events = list(
            (
                await session.scalars(
                    select(OutboxEvent).where(
                        OutboxEvent.aggregate_type == "recording-egress-operation",
                        OutboxEvent.aggregate_id == seed.operation_id,
                    )
                )
            ).all()
        )

    assert operation_count == 1
    assert operation is not None
    assert operation.start_state == "started"
    assert operation.provider_egress_id == egress_id
    assert call is not None
    assert call.recording_object_key == seed.expected_object_key
    assert call.recording_egress_id == egress_id
    assert len(webhook_rows) == 1
    assert webhook_rows[0].payload == {}
    assert 1 <= len(events) <= 2
    assert len({event.idempotency_key for event in events}) == len(events)
    assert all(
        event.payload == {"operation_id": str(seed.operation_id)} for event in events
    )


@pytest.mark.anyio
async def test_schedule_6_two_exact_matches_stay_conflicted_and_signal_once(
    recording_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    seed = await _seed_prepared_operation(
        recording_session_factory,
        suffix="two-exact-matches",
    )
    room_name = "task7-room-two-exact-matches"
    first_id = "task7-egress-exact-first"
    second_id = "task7-egress-exact-second"

    async with recording_session_factory() as seed_session:
        operation = await seed_session.get(
            RecordingEgressOperation,
            seed.operation_id,
        )
        call = await seed_session.get(Call, seed.call_id)
        event = await seed_session.scalar(
            select(OutboxEvent).where(
                OutboxEvent.aggregate_type == "recording-egress-operation",
                OutboxEvent.aggregate_id == seed.operation_id,
            )
        )
        assert operation is not None
        assert call is not None
        assert call.deleted_at is None
        assert event is not None
        operation.start_state = "uncertain"
        operation.start_attempted_at = FIXED_NOW
        await seed_session.commit()

    provider = _ExactListingProvider(
        (
            RecordingEgressSnapshot(
                egress_id=first_id,
                room_name=room_name,
                status=1,
                object_key=seed.expected_object_key,
            ),
            RecordingEgressSnapshot(
                egress_id=second_id,
                room_name=room_name,
                status=1,
                object_key=seed.expected_object_key,
            ),
        ),
        stop_failures=frozenset({first_id}),
    )
    observability = _RecordingSignalObservability()

    with pytest.raises(OutboxDeliveryError) as exc_info:
        await deliver_recording_reconcile(
            {
                "session_factory": recording_session_factory,
                "livekit_recording_provider": provider,
                "storage_provider": _RecordingStorage(),
                "recording_reconciliation_now": lambda: STOP_NOW,
                "observability": observability,
            },
            event,
        )

    assert exc_info.value.error_code == "recording_identity_conflict"
    assert exc_info.value.retryable is True
    assert exc_info.value.exhaustible is False
    assert provider.listed_rooms == [room_name]
    assert provider.stop_attempts == [first_id, second_id]
    assert observability.results == ["recording_identity_conflict"]
    assert observability.multiple_exact_count == 1

    async with recording_session_factory() as assertion_session:
        operation = await assertion_session.get(
            RecordingEgressOperation,
            seed.operation_id,
        )
        call = await assertion_session.get(Call, seed.call_id)

    assert operation is not None
    assert operation.start_state == "uncertain"
    assert operation.provider_egress_id is None
    assert operation.last_error_code == "recording_identity_conflict"
    assert call is not None
    assert call.deleted_at is None
    assert call.recording_object_key is None
    assert call.recording_egress_id is None
    assert call.recording_url is None


@pytest.mark.anyio
async def test_schedule_7_concurrent_repeated_owner_deletion_is_idempotent(
    recording_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    seed = await _seed_started_terminal_operation(
        recording_session_factory,
        suffix="repeated-owner-delete",
    )
    first_ready = asyncio.Event()
    second_ready = asyncio.Event()
    release = asyncio.Event()

    async def delete_owner_call(
        *,
        ready: asyncio.Event,
        requested_at: datetime,
    ) -> tuple[datetime, datetime]:
        async with recording_session_factory() as session:
            ready.set()
            await asyncio.wait_for(release.wait(), timeout=2)
            await CallHistoryService(
                session,
                recording_service=None,
                recording_lifecycle_service=RecordingLifecycleService(
                    session,
                    now_provider=lambda: requested_at,
                ),
            ).delete_call(seed.user_id, seed.call_id)
            operation = await session.get(
                RecordingEgressOperation,
                seed.operation_id,
            )
            assert operation is not None
            assert operation.stop_requested_at is not None
            assert operation.delete_requested_at is not None
            return operation.stop_requested_at, operation.delete_requested_at

    first_task = asyncio.create_task(
        delete_owner_call(ready=first_ready, requested_at=DELETION_NOW)
    )
    second_task = asyncio.create_task(
        delete_owner_call(
            ready=second_ready,
            requested_at=DELETION_NOW + timedelta(seconds=1),
        )
    )
    try:
        await asyncio.wait_for(first_ready.wait(), timeout=2)
        await asyncio.wait_for(second_ready.wait(), timeout=2)
        release.set()
        first_timestamps, second_timestamps = await asyncio.wait_for(
            asyncio.gather(first_task, second_task),
            timeout=2,
        )
    finally:
        await _release_and_cancel(first_task, release)
        await _release_and_cancel(second_task, release)

    assert tuple(map(_as_utc, first_timestamps)) == tuple(
        map(_as_utc, second_timestamps)
    )
    assert _as_utc(first_timestamps[0]) == _as_utc(first_timestamps[1])
    assert _as_utc(first_timestamps[0]) in {
        DELETION_NOW,
        DELETION_NOW + timedelta(seconds=1),
    }

    async with recording_session_factory() as session:
        operation_count = await session.scalar(
            select(func.count())
            .select_from(RecordingEgressOperation)
            .where(RecordingEgressOperation.call_id == seed.call_id)
        )
        operation = await session.get(RecordingEgressOperation, seed.operation_id)
        call = await session.get(Call, seed.call_id)
        delete_events = list(
            (
                await session.scalars(
                    select(OutboxEvent).where(
                        OutboxEvent.idempotency_key
                        == f"recording.reconcile:{seed.operation_id}:delete"
                    )
                )
            ).all()
        )

    assert operation_count == 1
    assert operation is not None
    assert operation.stop_requested_at is not None
    assert operation.delete_requested_at is not None
    assert _as_utc(operation.stop_requested_at) == _as_utc(first_timestamps[0])
    assert _as_utc(operation.delete_requested_at) == _as_utc(first_timestamps[1])
    assert call is not None
    assert call.deleted_at is not None
    assert call.recording_object_key is None
    assert call.recording_egress_id is None
    assert call.recording_url is None
    assert len(delete_events) == 1
    assert delete_events[0].payload == {"operation_id": str(seed.operation_id)}


@pytest.mark.anyio
async def test_schedule_8_stop_acceleration_cannot_steal_active_outbox_lease(
    recording_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    seed = await _seed_started_terminal_operation(
        recording_session_factory,
        suffix="active-outbox-lease",
    )
    lease_deadline = STOP_NOW + timedelta(minutes=5)
    attempt_count = 4
    async with recording_session_factory() as seed_session:
        processing_event = await seed_session.scalar(
            select(OutboxEvent)
            .where(
                OutboxEvent.aggregate_type == "recording-egress-operation",
                OutboxEvent.aggregate_id == seed.operation_id,
            )
            .order_by(OutboxEvent.created_at, OutboxEvent.id)
        )
        assert processing_event is not None
        processing_event.status = "processing"
        processing_event.attempt_count = attempt_count
        processing_event.next_attempt_at = lease_deadline
        processing_event.created_at = FIXED_NOW
        later_event = OutboxEvent(
            idempotency_key=(
                f"recording.reconcile:{seed.operation_id}:task7-later-phase"
            ),
            topic="recording.reconcile",
            aggregate_type="recording-egress-operation",
            aggregate_id=seed.operation_id,
            payload={"operation_id": str(seed.operation_id)},
            status="pending",
            attempt_count=0,
            next_attempt_at=STOP_NOW,
            created_at=FIXED_NOW + timedelta(seconds=1),
            updated_at=FIXED_NOW + timedelta(seconds=1),
        )
        seed_session.add(later_event)
        await seed_session.commit()
        processing_event_id = processing_event.id
        later_event_id = later_event.id

    stop_ready = asyncio.Event()
    claim_ready = asyncio.Event()
    release = asyncio.Event()

    async def request_stop() -> None:
        async with recording_session_factory() as session:
            call = await session.get(Call, seed.call_id)
            assert call is not None
            stop_ready.set()
            await asyncio.wait_for(release.wait(), timeout=2)
            requested = await RecordingLifecycleService(
                session,
                now_provider=lambda: STOP_NOW,
            ).request_stop(call)
            assert requested is not None
            await session.commit()

    async def claim_due_work() -> list[UUID]:
        async with recording_session_factory() as session:
            claim_ready.set()
            await asyncio.wait_for(release.wait(), timeout=2)
            claimed = await OutboxRepository(session).claim_batch(
                limit=10,
                now=STOP_NOW,
            )
            await session.commit()
            return [event.id for event in claimed]

    stop_task = asyncio.create_task(request_stop())
    claim_task = asyncio.create_task(claim_due_work())
    try:
        await asyncio.wait_for(stop_ready.wait(), timeout=2)
        await asyncio.wait_for(claim_ready.wait(), timeout=2)
        release.set()
        _, claimed_ids = await asyncio.wait_for(
            asyncio.gather(stop_task, claim_task),
            timeout=2,
        )
    finally:
        await _release_and_cancel(stop_task, release)
        await _release_and_cancel(claim_task, release)

    assert claimed_ids == []
    async with recording_session_factory() as session:
        processing = await session.get(OutboxEvent, processing_event_id)
        later = await session.get(OutboxEvent, later_event_id)
        events = list(
            (
                await session.scalars(
                    select(OutboxEvent).where(
                        OutboxEvent.aggregate_type == "recording-egress-operation",
                        OutboxEvent.aggregate_id == seed.operation_id,
                    )
                )
            ).all()
        )

    assert processing is not None
    assert processing.status == "processing"
    assert processing.attempt_count == attempt_count
    assert _as_utc(processing.next_attempt_at) == lease_deadline
    assert later is not None
    assert later.status == "pending"
    assert later.attempt_count == 0
    assert all(
        event.status == "pending" and event.attempt_count == 0
        for event in events
        if event.id != processing_event_id
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "later_tombstone",
    [False, True],
    ids=["committed-delete-snapshot", "later-call-tombstone"],
)
async def test_schedule_9_two_stale_restorers_merge_after_reclaimed_cleanup(
    recording_session_factory: async_sessionmaker[AsyncSession],
    *,
    later_tombstone: bool,
) -> None:
    seed = await _seed_terminal_starting_operation(recording_session_factory)
    expected_stop_at = STOP_NOW if later_tombstone else DELETION_NOW
    call_tombstone_at: datetime | None = None

    async with recording_session_factory() as intent_session:
        operation = await intent_session.get(
            RecordingEgressOperation,
            seed.operation_id,
        )
        call = await intent_session.get(Call, seed.call_id)
        assert operation is not None
        assert call is not None
        operation.start_attempted_at = DELETION_NOW
        if later_tombstone:
            requested = await RecordingLifecycleService(
                intent_session,
                now_provider=lambda: STOP_NOW,
            ).request_stop(call)
            assert requested is operation
            await intent_session.commit()
        else:
            await intent_session.commit()
            await CallHistoryService(
                intent_session,
                recording_service=None,
                recording_lifecycle_service=RecordingLifecycleService(
                    intent_session,
                    now_provider=lambda: DELETION_NOW,
                ),
            ).delete_call(seed.user_id, seed.call_id)

    if not later_tombstone:
        async with recording_session_factory() as tombstone_session:
            tombstoned_call = await tombstone_session.get(Call, seed.call_id)
            assert tombstoned_call is not None
            assert tombstoned_call.deleted_at is not None
            call_tombstone_at = _as_utc(tombstoned_call.deleted_at)

    async with recording_session_factory() as lease_session:
        stale_event = await lease_session.scalar(
            select(OutboxEvent).where(
                OutboxEvent.idempotency_key
                == f"recording.reconcile:{seed.operation_id}:start"
            )
        )
        assert stale_event is not None
        stale_event.status = "processing"
        stale_event.attempt_count = 1
        stale_event.next_attempt_at = STOP_NOW
        stale_event.created_at = FIXED_NOW
        await lease_session.commit()
        stale_event_id = stale_event.id

    first_id = "task7-egress-stale-restorer-a"
    second_id = "task7-egress-stale-restorer-b"
    room_name = "task7-room-simultaneous-success-delete"
    first_provider = _BlockingListingProvider(
        snapshot=RecordingEgressSnapshot(
            egress_id=first_id,
            room_name=room_name,
            status=1,
            object_key=seed.expected_object_key,
        )
    )
    second_provider = _BlockingListingProvider(
        snapshot=RecordingEgressSnapshot(
            egress_id=second_id,
            room_name=room_name,
            status=1,
            object_key=seed.expected_object_key,
        )
    )
    first_outer_storage = _RecordingStorage()
    second_outer_storage = _RecordingStorage()
    first_reconciler = RecordingReconciler(
        recording_session_factory,
        first_provider,
        first_outer_storage,
        now_provider=lambda: DELETION_NOW + timedelta(seconds=1),
    )
    second_reconciler = RecordingReconciler(
        recording_session_factory,
        second_provider,
        second_outer_storage,
        now_provider=lambda: DELETION_NOW + timedelta(seconds=2),
    )
    first_task = asyncio.create_task(first_reconciler.reconcile(seed.operation_id))
    second_task = asyncio.create_task(second_reconciler.reconcile(seed.operation_id))

    cleanup_storage = _RecordingStorage()
    try:
        await asyncio.wait_for(first_provider.entered.wait(), timeout=2)
        await asyncio.wait_for(second_provider.entered.wait(), timeout=2)

        if later_tombstone:
            async with recording_session_factory() as delete_session:
                await CallHistoryService(
                    delete_session,
                    recording_service=None,
                    recording_lifecycle_service=RecordingLifecycleService(
                        delete_session,
                        now_provider=lambda: DELETION_NOW,
                    ),
                ).delete_call(seed.user_id, seed.call_id)
                tombstoned_call = await delete_session.get(Call, seed.call_id)
                assert tombstoned_call is not None
                assert tombstoned_call.deleted_at is not None
                call_tombstone_at = _as_utc(tombstoned_call.deleted_at)

        cleanup_at = DELETION_NOW + timedelta(seconds=3)
        async with recording_session_factory() as reclaim_session:
            reclaimed = await OutboxRepository(reclaim_session).claim_batch(
                limit=1,
                now=cleanup_at,
            )
            assert [event.id for event in reclaimed] == [stale_event_id]
            reclaimed_attempt = reclaimed[0].attempt_count
            assert reclaimed_attempt == 2
            await reclaim_session.commit()

        async with recording_session_factory() as result_session:
            changed = await RecordingLifecycleService(
                result_session,
                now_provider=lambda: cleanup_at,
            ).record_start_error(
                seed.operation_id,
                outcome="not_started",
                error_code="validation",
            )
            assert changed is not None
            await result_session.commit()

        cleanup_result = await RecordingReconciler(
            recording_session_factory,
            _NoListingProvider(),
            cleanup_storage,
            now_provider=lambda: cleanup_at + timedelta(seconds=1),
        ).reconcile(seed.operation_id)
        assert cleanup_result.outcome == "complete"
        assert cleanup_result.error_code is None

        async with recording_session_factory() as delivery_session:
            delivered = await OutboxRepository(delivery_session).mark_delivered(
                event_id=stale_event_id,
                attempt_count=reclaimed_attempt,
                delivered_at=cleanup_at + timedelta(seconds=2),
            )
            assert delivered is not None
            await delivery_session.commit()

        async with recording_session_factory() as removed_session:
            assert (
                await removed_session.get(
                    RecordingEgressOperation,
                    seed.operation_id,
                )
                is None
            )

        first_provider.resume.set()
        second_provider.resume.set()
        first_result, second_result = await asyncio.wait_for(
            asyncio.gather(first_task, second_task),
            timeout=2,
        )
    finally:
        await _release_and_cancel(
            first_task,
            first_provider.resume,
            second_provider.resume,
        )
        await _release_and_cancel(
            second_task,
            first_provider.resume,
            second_provider.resume,
        )

    for result in (first_result, second_result):
        assert result.outcome == "retry"
        assert result.error_code == "recording_identity_conflict"
    assert first_provider.listed_rooms == [room_name]
    assert second_provider.listed_rooms == [room_name]
    assert set(first_provider.stopped_ids + second_provider.stopped_ids) == {
        first_id,
        second_id,
    }
    assert cleanup_storage.deleted_keys == [seed.expected_object_key]
    assert first_outer_storage.deleted_keys == []
    assert second_outer_storage.deleted_keys == []

    async with recording_session_factory() as session:
        operations = list(
            (
                await session.scalars(
                    select(RecordingEgressOperation).where(
                        RecordingEgressOperation.call_id == seed.call_id
                    )
                )
            ).all()
        )
        recovery_events = list(
            (
                await session.scalars(
                    select(OutboxEvent).where(
                        OutboxEvent.idempotency_key
                        == (
                            f"recording.reconcile:{seed.operation_id}:"
                            "missing-operation-conflict"
                        )
                    )
                )
            ).all()
        )
        recording_events = list(
            (
                await session.scalars(
                    select(OutboxEvent).where(
                        OutboxEvent.aggregate_type == "recording-egress-operation",
                        OutboxEvent.aggregate_id == seed.operation_id,
                    )
                )
            ).all()
        )
        call = await session.get(Call, seed.call_id)
        delivered_stale_event = await session.get(OutboxEvent, stale_event_id)

    assert len(operations) == 1
    restored = operations[0]
    assert restored.id == seed.operation_id
    assert restored.call_id == seed.call_id
    assert restored.room_name == room_name
    assert restored.expected_object_key == seed.expected_object_key
    assert restored.start_state == "started"
    assert restored.provider_egress_id in {first_id, second_id}
    assert restored.last_error_code == "recording_identity_conflict"
    assert restored.stop_requested_at is not None
    assert restored.delete_requested_at is not None
    assert _as_utc(restored.stop_requested_at) == expected_stop_at
    assert call_tombstone_at is not None
    expected_restored_delete_at = call_tombstone_at if later_tombstone else DELETION_NOW
    assert _as_utc(restored.delete_requested_at) == expected_restored_delete_at
    assert restored.object_deleted_at is None
    assert len(recovery_events) == 1
    assert recovery_events[0].payload == {"operation_id": str(seed.operation_id)}
    assert recovery_events[0].status == "pending"
    assert all(
        event.payload == {"operation_id": str(seed.operation_id)}
        for event in recording_events
    )
    assert delivered_stale_event is not None
    assert delivered_stale_event.status == "delivered"
    assert delivered_stale_event.attempt_count == 2
    assert call is not None
    assert call.deleted_at is not None
    assert _as_utc(call.deleted_at) == call_tombstone_at
    assert call.recording_object_key is None
    assert call.recording_egress_id is None
    assert call.recording_url is None
