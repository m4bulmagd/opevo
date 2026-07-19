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
from sqlalchemy import select, text
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
from app.providers.livekit_recording.base import RecordingEgressResult
from app.services.call_history_service import CallHistoryService
from app.services.call_lifecycle_service import CallLifecycleService
from app.services.recording_lifecycle_service import (
    RecordingLifecycleService,
    RecordingStartClaim,
)


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
