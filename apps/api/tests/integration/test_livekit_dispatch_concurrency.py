import asyncio
import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base
from app.models.agent_config import AgentConfig
from app.models.call import Call
from app.models.outbox_event import OutboxEvent
from app.models.phone_number import PhoneNumber
from app.models.recording_egress_operation import RecordingEgressOperation
from app.models.subscription import Subscription
from app.models.usage_ledger import UsageLedger
from app.models.user import User
from app.repositories.call_repository import CallRepository
from app.services.livekit_dispatch_service import LiveKitDispatchService
from app.services.call_history_service import CallHistoryService
from app.services.call_lifecycle_service import CallLifecycleService
from app.services.recording_lifecycle_service import RecordingLifecycleService


@pytest.fixture(autouse=True)
def _legacy_normal_call_flow(monkeypatch: pytest.MonkeyPatch):
    from app.core.config import get_settings

    monkeypatch.setenv("ACTIVATION_FLOW_ENABLED", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest_asyncio.fixture
async def livekit_session_factory():
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("PostgreSQL dispatch concurrency test requires TEST_DATABASE_URL")
    if database_url.startswith("postgresql://"):
        database_url = database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if not database_url.startswith("postgresql+asyncpg://"):
        pytest.skip("TEST_DATABASE_URL must identify PostgreSQL")

    schema_name = f"task8_dispatch_{uuid4().hex}"
    quoted_schema = f'"{schema_name}"'
    admin = create_async_engine(database_url, isolation_level="AUTOCOMMIT")
    engine = None
    try:
        async with admin.connect() as connection:
            await connection.execute(text(f"CREATE SCHEMA {quoted_schema}"))
        engine = create_async_engine(
            database_url,
            connect_args={"server_settings": {"search_path": schema_name}},
        )
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        if engine is not None:
            await engine.dispose()
        async with admin.connect() as connection:
            await connection.execute(text(f"DROP SCHEMA IF EXISTS {quoted_schema} CASCADE"))
        await admin.dispose()


class _Realtime:
    async def publish_call_started(self, *_args, **_kwargs) -> None:
        return None


class _Recording:
    def __init__(self) -> None:
        self.starts: list[dict] = []

    async def start_room_recording(self, **_kwargs):
        self.starts.append(_kwargs)
        return SimpleNamespace(object_key="unused", egress_id="unused", url=None)

    async def stop_room_recording(self, **_kwargs) -> None:
        return None


class _PausingCallRepository(CallRepository):
    def __init__(self, session, *, selected: asyncio.Event, resume: asyncio.Event) -> None:
        super().__init__(session)
        self.selected = selected
        self.resume = resume

    async def get_pending_by_room_without_recording(self, *, room_name: str):
        call = await super().get_pending_by_room_without_recording(room_name=room_name)
        self.selected.set()
        await self.resume.wait()
        return call


class _BlockingRecording:
    def __init__(self, *, entered: asyncio.Event, resume: asyncio.Event) -> None:
        self.entered = entered
        self.resume = resume
        self.starts: list[dict] = []

    async def start_room_recording(self, *, room_name: str, object_key: str):
        self.starts.append({"room_name": room_name, "object_key": object_key})
        self.entered.set()
        await self.resume.wait()
        return SimpleNamespace(
            object_key=object_key,
            egress_id="egress-delete-race",
            url=None,
        )


class _PausingBeginLifecycle(RecordingLifecycleService):
    def __init__(
        self,
        session,
        *,
        entered: asyncio.Event,
        resume: asyncio.Event,
    ) -> None:
        super().__init__(session)
        self.entered = entered
        self.resume = resume

    async def begin_start(self, operation_id):
        self.entered.set()
        await self.resume.wait()
        return await super().begin_start(operation_id)


@pytest.mark.anyio
async def test_concurrent_distinct_joins_create_one_call_and_one_intent(
    livekit_session_factory,
) -> None:
    now = datetime.now(UTC)
    async with livekit_session_factory() as session:
        user = User(clerk_user_id="concurrent-user", email="concurrent@example.com")
        session.add(user)
        await session.flush()
        session.add_all(
            [
                PhoneNumber(
                    user_id=user.id,
                    e164="+33999888777",
                    country_code="FR",
                    provider="telnyx",
                    provider_number_id="number-concurrent",
                    provider_connection_name="app-active",
                    is_active=True,
                ),
                AgentConfig(
                    user_id=user.id,
                    agent_name="Ava",
                    owner_context="Sam at Bakery",
                    system_prompt="Be helpful",
                    knowledge_base="Hours 9-5",
                    pipeline_mode="stt_llm_tts",
                    is_enabled=True,
                ),
                Subscription(
                    user_id=user.id,
                    stripe_customer_id="cus-concurrent",
                    stripe_subscription_id="sub-concurrent",
                    plan_tier="starter",
                    status="active",
                    allocated_minutes=60,
                    current_period_start=now - timedelta(days=1),
                    current_period_end=now + timedelta(days=1),
                ),
                UsageLedger(
                    user_id=user.id,
                    event_type="invoice_paid_reset",
                    source_id="invoice-concurrent",
                    minutes_delta=60,
                    balance_after=60,
                ),
            ]
        )
        await session.commit()

    async def join(room_name: str) -> str:
        async with livekit_session_factory() as session:
            result = await LiveKitDispatchService(
                session,
                realtime_service=_Realtime(),
                recording_service=_Recording(),
            ).handle_participant_joined(
                {
                    "event": "participant_joined",
                    "room": {"name": room_name},
                    "participant": {
                        "identity": f"caller-{room_name}",
                        "kind": "SIP",
                        "attributes": {
                            "sip.phoneNumber": "+33123456789",
                            "sip.trunkPhoneNumber": "+33999888777",
                        },
                    },
                }
            )
            return result.status

    outcomes = await asyncio.gather(join("room-a"), join("room-b"))

    assert sorted(outcomes) == ["accepted", "busy"]
    async with livekit_session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(Call)) == 1
        assert await session.scalar(select(func.count()).select_from(OutboxEvent)) == 1


@pytest.mark.anyio
async def test_agent_join_cannot_resurrect_call_failed_after_stale_pending_read(
    livekit_session_factory,
) -> None:
    async with livekit_session_factory() as session:
        user = User(clerk_user_id="agent-race-user", email="agent-race@example.com")
        session.add(user)
        await session.flush()
        call = Call(
            user_id=user.id,
            livekit_room_id="room-agent-race",
            status="pending",
        )
        session.add(call)
        await session.commit()
        call_id = call.id

    selected = asyncio.Event()
    resume = asyncio.Event()
    recording = _Recording()

    async with livekit_session_factory() as join_session:
        join_service = LiveKitDispatchService(
            join_session,
            call_repository=_PausingCallRepository(
                join_session,
                selected=selected,
                resume=resume,
            ),
            realtime_service=_Realtime(),
            recording_service=recording,
        )
        join_task = asyncio.create_task(
            join_service.handle_participant_joined(
                {
                    "event": "participant_joined",
                    "room": {"name": "room-agent-race"},
                    "participant": {
                        "identity": f"agent-call-{call_id}",
                        "kind": "AGENT",
                        "attributes": {},
                    },
                }
            )
        )
        await asyncio.wait_for(selected.wait(), timeout=1)

        async with livekit_session_factory() as terminal_session:
            terminal_call = await CallRepository(terminal_session).get_by_id_for_update(
                call_id
            )
            assert terminal_call is not None
            await CallRepository(terminal_session).mark_dispatch_failed(
                terminal_call,
                failure_code="dispatch_configuration",
            )
            await terminal_session.commit()

        resume.set()
        result = await asyncio.wait_for(join_task, timeout=1)

    async with livekit_session_factory() as session:
        stored = await session.get(Call, call_id)
        assert stored is not None
        assert stored.status == "failed"
        assert stored.failure_code == "dispatch_configuration"
        assert stored.started_at is None
    assert result.status == "ignored"
    assert recording.starts == []


@pytest.mark.anyio
async def test_end_before_begin_claim_makes_start_ineligible_without_provider_io(
    livekit_session_factory,
) -> None:
    async with livekit_session_factory() as session:
        user = User(
            clerk_user_id="end-before-claim-user",
            email="end-before-claim@example.com",
        )
        session.add(user)
        await session.flush()
        call = Call(
            user_id=user.id,
            livekit_room_id="room-end-before-claim",
            status="pending",
        )
        session.add(call)
        await session.commit()
        call_id = call.id

    entered = asyncio.Event()
    resume = asyncio.Event()
    recording = _Recording()
    async with livekit_session_factory() as dispatch_session:
        task = asyncio.create_task(
            LiveKitDispatchService(
                dispatch_session,
                realtime_service=None,
                recording_service=recording,
                recording_lifecycle_service=_PausingBeginLifecycle(
                    dispatch_session,
                    entered=entered,
                    resume=resume,
                ),
            ).handle_participant_joined(
                {
                    "event": "participant_joined",
                    "room": {"name": "room-end-before-claim"},
                    "participant": {
                        "identity": f"agent-call-{call_id}",
                        "kind": "AGENT",
                        "attributes": {},
                    },
                }
            )
        )
        await asyncio.wait_for(entered.wait(), timeout=1)

        async with livekit_session_factory() as terminal_session:
            await CallLifecycleService(terminal_session).end_from_agent(
                call_id=call_id,
                duration_seconds=1,
            )
            await terminal_session.commit()

        resume.set()
        result = await asyncio.wait_for(task, timeout=1)

    async with livekit_session_factory() as session:
        stored_call = await session.get(Call, call_id)
        operation = await session.scalar(
            select(RecordingEgressOperation).where(
                RecordingEgressOperation.call_id == call_id
            )
        )
    assert result.status == "connected"
    assert recording.starts == []
    assert stored_call is not None
    assert stored_call.status == "ending"
    assert operation is not None
    assert operation.start_state == "prepared"
    assert operation.stop_requested_at is not None


@pytest.mark.anyio
async def test_end_after_claim_then_delete_purges_call_and_late_success_only_updates_operation(
    livekit_session_factory,
) -> None:
    now = datetime.now(UTC)
    async with livekit_session_factory() as session:
        user = User(
            clerk_user_id="delete-start-race-user",
            email="delete-start-race@example.com",
        )
        session.add(user)
        await session.flush()
        call = Call(
            user_id=user.id,
            livekit_room_id="room-delete-start-race",
            caller_number="+33199000000",
            summary_text="private summary",
            status="pending",
        )
        session.add_all(
            [
                call,
                UsageLedger(
                    user_id=user.id,
                    event_type="subscription_activated",
                    source_id="in_delete_start_race",
                    minutes_delta=1,
                    balance_after=1,
                ),
            ]
        )
        await session.commit()
        call_id = call.id
        user_id = user.id

    entered = asyncio.Event()
    resume = asyncio.Event()
    provider = _BlockingRecording(entered=entered, resume=resume)
    async with livekit_session_factory() as dispatch_session:
        dispatch_task = asyncio.create_task(
            LiveKitDispatchService(
                dispatch_session,
                realtime_service=None,
                recording_service=provider,
            ).handle_participant_joined(
                {
                    "event": "participant_joined",
                    "room": {"name": "room-delete-start-race"},
                    "participant": {
                        "identity": f"agent-call-{call_id}",
                        "kind": "AGENT",
                        "attributes": {},
                    },
                }
            )
        )
        await asyncio.wait_for(entered.wait(), timeout=1)

        async with livekit_session_factory() as delete_session:
            lifecycle = CallLifecycleService(delete_session)
            await lifecycle.end_from_agent(
                call_id=call_id,
                duration_seconds=1,
                ended_at=now + timedelta(seconds=1),
            )
            await delete_session.commit()
            claim = await lifecycle.claim_finalization(call_id)
            await lifecycle.complete_finalization(
                call_id,
                generation=claim.generation,
            )
            await CallHistoryService(
                delete_session,
                recording_service=None,
                recording_lifecycle_service=RecordingLifecycleService(
                    delete_session
                ),
            ).delete_call(user_id, call_id)

        resume.set()
        result = await asyncio.wait_for(dispatch_task, timeout=1)

    async with livekit_session_factory() as session:
        stored_call = await session.get(Call, call_id)
        operation = await session.scalar(
            select(RecordingEgressOperation).where(
                RecordingEgressOperation.call_id == call_id
            )
        )
    assert result.status == "connected"
    assert len(provider.starts) == 1
    assert stored_call is not None
    assert stored_call.deleted_at is not None
    assert stored_call.caller_number is None
    assert stored_call.summary_text is None
    assert stored_call.recording_object_key is None
    assert stored_call.recording_egress_id is None
    assert operation is not None
    assert operation.start_state == "started"
    assert operation.provider_egress_id == "egress-delete-race"
    assert operation.stop_requested_at is not None
    assert operation.delete_requested_at is not None
