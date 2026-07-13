"""Cross-layer crash-boundary proof using the real agent SessionRuntime.

The test imports the sibling agent package deliberately: it exercises the real
sequencing/flusher/finalization code against the real API routes and database,
while replacing only network transport and external providers.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker


AGENT_APP_DIR = Path(__file__).resolve().parents[3] / "agent"
if str(AGENT_APP_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_APP_DIR))

from agent.api_client import AgentApiClient  # noqa: E402
from agent.schemas import DispatchMetadata  # noqa: E402
from agent.session_runtime import SessionRuntime  # noqa: E402

from app.core.database import get_session  # noqa: E402
from app.core.dispatch_token import create_dispatch_token  # noqa: E402
from app.models.agent_config import AgentConfig  # noqa: E402
from app.models.call import Call  # noqa: E402
from app.models.call_message import CallMessage  # noqa: E402
from app.models.usage_ledger import UsageLedger  # noqa: E402
from app.repositories.call_repository import CallRepository  # noqa: E402
from app.repositories.message_repository import MessageRepository  # noqa: E402
from app.services.call_lifecycle_service import CallLifecycleService  # noqa: E402
from app.services.recording_service import RecordingResult  # noqa: E402
from app.services.usage_accounting_service import UsageAccountingService  # noqa: E402


class NoopEventPublisher:
    async def publish(self, _payload: dict) -> None:
        return None


class CapturingSummaryService:
    def __init__(self) -> None:
        self.transcripts: list[list[dict]] = []

    async def create_summary(self, payload: dict):
        self.transcripts.append(payload["transcript"])
        return SimpleNamespace(
            text="Complete summary",
            data={
                "summary_text": "Complete summary",
                "caller_intent": "Test durability",
                "action_items": [],
                "sentiment": "neutral",
                "follow_up_required": False,
            },
            job_enqueued=True,
        )


class NoopRecordingService:
    async def store_recording(self, _payload: dict) -> RecordingResult:
        return RecordingResult(object_key=None, url=None, job_enqueued=False)


class NoopNotificationService:
    async def create_call_completed_notification(self, **_kwargs):
        return SimpleNamespace(job_enqueued=False)


class NoopPhoneRepository:
    async def get_by_user_id(self, _user_id):
        return None


class NoopTelephonyService:
    async def disable_number(self, _user_id):
        return None


class CapturingFinalizationQueue:
    def __init__(self) -> None:
        self.payloads: list[dict] = []

    async def enqueue(self, payload: dict) -> str:
        self.payloads.append(payload)
        return f"call-finalization:{payload['call_id']}"


class BlockThirdAppendClient:
    def __init__(self, client: AgentApiClient) -> None:
        self.client = client
        self.third_started = asyncio.Event()
        self.never_release_third = asyncio.Event()

    async def append_transcript(self, call_id, dispatch_token, item):
        if item.sequence_number == 3:
            self.third_started.set()
            await self.never_release_third.wait()
        return await self.client.append_transcript(call_id, dispatch_token, item)

    async def complete_call(self, payload: dict) -> dict:
        return await self.client.complete_call(payload)


async def _wait_until(predicate, *, attempts: int = 200) -> None:
    for _ in range(attempts):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError("condition was not reached")


@pytest.mark.anyio
async def test_real_agent_runtime_acknowledged_rows_and_recovery_tail_form_full_summary(
    db_session,
    active_user,
) -> None:
    config = AgentConfig(
        user_id=active_user.id,
        agent_name="Durability agent",
        system_prompt="Be helpful",
        knowledge_base="",
        is_enabled=True,
    )
    db_session.add(config)
    await db_session.flush()
    call = Call(
        id=uuid4(),
        user_id=active_user.id,
        agent_config_id=config.id,
        status="connected",
    )
    db_session.add_all(
        [
            call,
            UsageLedger(
                user_id=active_user.id,
                event_type="subscription_activated",
                source_id=f"in_runtime_durability_{call.id}",
                minutes_delta=10,
                balance_after=10,
            ),
        ]
    )
    await db_session.commit()
    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    summary_service = CapturingSummaryService()

    from app.routers.agent import router

    async def override_session():
        async with session_factory() as session:
            yield session

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_session
    finalization_queue = CapturingFinalizationQueue()
    app.state.call_finalization_queue = finalization_queue
    transport = httpx.ASGITransport(app=app)
    token = create_dispatch_token(
        call_id=str(call.id),
        user_id=str(active_user.id),
        agent_config_id=str(config.id),
    )
    metadata = DispatchMetadata(
        call_id=str(call.id),
        user_id=str(active_user.id),
        agent_config_id=str(config.id),
        agent_identity=f"agent-call-{call.id}",
        agent_name="Durability agent",
        owner_name="Owner",
        system_prompt="Be helpful",
        knowledge_base="",
        pipeline_mode="stt_llm_tts",
        minutes_remaining=10,
        dispatch_token=token,
    )

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as http_client:
        api_client = BlockThirdAppendClient(
            AgentApiClient(
                base_url="http://testserver",
                agent_token=None,
                http_client=http_client,
                max_retries=1,
            )
        )
        runtime = SessionRuntime(
            NoopEventPublisher(),
            api_client=api_client,
            finalize_timeout_seconds=0.01,
        )
        await runtime.handle_caller_transcript(metadata, "first")
        await runtime.handle_agent_utterance(metadata, "second")
        await runtime.handle_caller_transcript(metadata, "third")
        await api_client.third_started.wait()
        await _wait_until(
            lambda: [item.sequence_number for item in runtime.pending_transcript] == [3]
        )

        async with session_factory() as session:
            acknowledged = list(
                (
                    await session.execute(
                        select(CallMessage)
                        .where(CallMessage.call_id == call.id)
                        .order_by(CallMessage.sequence_number)
                    )
                ).scalars()
            )
        assert [(row.sequence_number, row.text) for row in acknowledged] == [
            (1, "first"),
            (2, "second"),
        ]

        await runtime.finalize(metadata, duration_seconds=3)

    assert len(finalization_queue.payloads) == 1
    async with session_factory() as session:
        await CallLifecycleService(
            session,
            call_repository=CallRepository(session),
            message_repository=MessageRepository(session),
            usage_accounting_service=UsageAccountingService(session),
            phone_number_repository=NoopPhoneRepository(),
            telephony_service=NoopTelephonyService(),
            summary_service=summary_service,
            recording_service=NoopRecordingService(),
            notification_service=NoopNotificationService(),
        ).finalize_call(finalization_queue.payloads[0])

    async with session_factory() as session:
        durable = list(
            (
                await session.execute(
                    select(CallMessage)
                    .where(CallMessage.call_id == call.id)
                    .order_by(CallMessage.sequence_number)
                )
            ).scalars()
        )
    assert [(row.sequence_number, row.speaker, row.text) for row in durable] == [
        (1, "CALLER", "first"),
        (2, "AGENT", "second"),
        (3, "CALLER", "third"),
    ]
    assert summary_service.transcripts == [
        [
            {"sequence_number": 1, "speaker": "CALLER", "text": "first"},
            {"sequence_number": 2, "speaker": "AGENT", "text": "second"},
            {"sequence_number": 3, "speaker": "CALLER", "text": "third"},
        ]
    ]
