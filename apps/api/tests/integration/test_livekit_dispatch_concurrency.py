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
from app.models.subscription import Subscription
from app.models.usage_ledger import UsageLedger
from app.models.user import User
from app.repositories.call_repository import CallRepository
from app.services.livekit_dispatch_service import LiveKitDispatchService


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
