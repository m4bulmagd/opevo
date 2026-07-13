from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.agent_config import AgentConfig
from app.models.call import Call
from app.models.outbox_event import OutboxEvent
from app.models.phone_number import PhoneNumber
from app.models.subscription import Subscription
from app.models.usage_ledger import UsageLedger
from app.services.livekit_dispatch_service import LiveKitDispatchService
from app.workers.jobs.outbox_topics import deliver_recording_stop


class _ForbiddenDirectDispatch:
    def __init__(self) -> None:
        self.calls = 0

    async def create_dispatch(self, **_kwargs) -> None:
        self.calls += 1
        raise AssertionError("webhook path must not call LiveKit")


class _Realtime:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def publish_call_started(self, user_id: str, *, room_name: str, call_id: str) -> None:
        self.events.append(
            {"user_id": user_id, "room_name": room_name, "call_id": call_id}
        )


class _Recording:
    def __init__(self) -> None:
        self.starts: list[dict] = []
        self.stops: list[str] = []

    async def start_room_recording(self, *, room_name, user_id, call_id):
        self.starts.append(
            {"room_name": room_name, "user_id": user_id, "call_id": call_id}
        )
        return SimpleNamespace(
            object_key=f"calls/{user_id}/{call_id}.ogg",
            egress_id="egress-1",
            url=None,
        )

    async def stop_room_recording(self, *, egress_id: str) -> None:
        self.stops.append(egress_id)

    async def ensure_stopped(self, egress_id: str) -> None:
        self.stops.append(egress_id)


class _CommitAwareRecording(_Recording):
    def __init__(self, session) -> None:
        super().__init__()
        self.session = session

    async def start_room_recording(self, *, room_name, user_id, call_id):
        assert self.session.in_transaction() is False
        return await super().start_room_recording(
            room_name=room_name,
            user_id=user_id,
            call_id=call_id,
        )


class _CompletingRecording(_Recording):
    def __init__(self, session_factory) -> None:
        super().__init__()
        self.session_factory = session_factory

    async def start_room_recording(self, *, room_name, user_id, call_id):
        recording = await super().start_room_recording(
            room_name=room_name,
            user_id=user_id,
            call_id=call_id,
        )
        async with self.session_factory() as session:
            call = await session.get(Call, call_id)
            assert call is not None
            call.status = "completed"
            call.ended_at = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)
            call.duration_seconds = 17
            call.minutes_charged = 1
            await session.commit()
        return recording


class _FailingCleanupRecording(_CompletingRecording):
    async def ensure_stopped(self, egress_id: str) -> None:
        self.stops.append(egress_id)
        raise RuntimeError("cleanup unavailable")


class _Pool:
    def __init__(self) -> None:
        self.jobs: list[tuple[str, dict]] = []

    async def enqueue_job(self, name: str, payload: dict) -> None:
        self.jobs.append((name, payload))


async def _seed_eligible_user(db_session):
    from app.models.user import User

    now = datetime.now(UTC)
    user = User(clerk_user_id="dispatch-user", email="dispatch@example.com")
    db_session.add(user)
    await db_session.flush()
    phone = PhoneNumber(
        user_id=user.id,
        e164="+33999888777",
        country_code="FR",
        provider="telnyx",
        provider_number_id="number-1",
        provider_connection_name="app-active",
        is_active=True,
    )
    config = AgentConfig(
        user_id=user.id,
        agent_name="Ava",
        owner_context="Sam at Bakery",
        system_prompt="Be helpful",
        knowledge_base="Hours 9-5",
        pipeline_mode="stt_llm_tts",
        is_enabled=True,
    )
    db_session.add_all(
        [
            phone,
            config,
            Subscription(
                user_id=user.id,
                stripe_customer_id="cus-dispatch",
                stripe_subscription_id="sub-dispatch",
                plan_tier="starter",
                status="active",
                allocated_minutes=60,
                current_period_start=now - timedelta(days=1),
                current_period_end=now + timedelta(days=1),
            ),
            UsageLedger(
                user_id=user.id,
                event_type="invoice_paid_reset",
                source_id="invoice-dispatch",
                minutes_delta=60,
                balance_after=60,
            ),
        ]
    )
    await db_session.commit()
    return user, phone, config


def _sip_join(*, room: str = "room-1", trunk: str | None = "+33999888777") -> dict:
    attributes = {"sip.phoneNumber": "+33123456789"}
    if trunk is not None:
        attributes["sip.trunkPhoneNumber"] = trunk
    return {
        "event": "participant_joined",
        "room": {"name": room},
        "participant": {
            "identity": "caller",
            "kind": "SIP",
            "attributes": attributes,
        },
    }


@pytest.mark.anyio
async def test_sip_join_commits_call_and_dispatch_intent_without_provider_io(db_session) -> None:
    user, phone, config = await _seed_eligible_user(db_session)
    direct = _ForbiddenDirectDispatch()
    realtime = _Realtime()
    pool = _Pool()
    service = LiveKitDispatchService(
        db_session,
        direct,
        realtime_service=realtime,
        recording_service=_Recording(),
        arq_pool=pool,
    )

    result = await service.handle_participant_joined(_sip_join())

    calls = list((await db_session.execute(select(Call))).scalars())
    events = list((await db_session.execute(select(OutboxEvent))).scalars())
    assert result.status == "accepted"
    assert len(calls) == len(events) == 1
    assert calls[0].user_id == user.id
    assert calls[0].phone_number_id == phone.id
    assert calls[0].agent_config_id == config.id
    assert events[0].topic == "livekit.dispatch"
    assert events[0].aggregate_type == "call"
    assert events[0].aggregate_id == calls[0].id
    assert events[0].payload == {"call_id": str(calls[0].id)}
    assert direct.calls == 0
    assert pool.jobs == [("outbox_delivery_job", {})]
    assert realtime.events == [
        {
            "user_id": str(user.id),
            "room_name": "room-1",
            "call_id": str(calls[0].id),
        }
    ]


@pytest.mark.anyio
async def test_same_room_replay_is_idempotent(db_session) -> None:
    await _seed_eligible_user(db_session)
    service = LiveKitDispatchService(
        db_session,
        _ForbiddenDirectDispatch(),
        realtime_service=_Realtime(),
        recording_service=_Recording(),
        arq_pool=_Pool(),
    )

    first = await service.handle_participant_joined(_sip_join())
    second = await service.handle_participant_joined(_sip_join())

    assert first.status == "accepted"
    assert second.status == "idempotent"
    assert await db_session.scalar(select(func.count()).select_from(Call)) == 1
    assert await db_session.scalar(select(func.count()).select_from(OutboxEvent)) == 1


@pytest.mark.anyio
@pytest.mark.parametrize(
    "event",
    [
        _sip_join(trunk=None),
        {
            **_sip_join(),
            "participant": {
                "identity": "observer",
                "kind": "STANDARD",
                "attributes": {
                    "sip.phoneNumber": "+33123456789",
                    "sip.trunkPhoneNumber": "+33999888777",
                },
            },
        },
    ],
)
async def test_missing_trunk_or_forged_sip_attributes_create_no_intent(
    db_session,
    event: dict,
) -> None:
    await _seed_eligible_user(db_session)
    recording = _Recording()
    service = LiveKitDispatchService(
        db_session,
        _ForbiddenDirectDispatch(),
        realtime_service=_Realtime(),
        recording_service=recording,
    )

    result = await service.handle_participant_joined(event)

    assert result.status == "ignored"
    assert await db_session.scalar(select(func.count()).select_from(Call)) == 0
    assert await db_session.scalar(select(func.count()).select_from(OutboxEvent)) == 0
    assert recording.starts == []


@pytest.mark.anyio
async def test_ineligible_subscription_creates_no_call_or_outbox(db_session) -> None:
    await _seed_eligible_user(db_session)
    subscription = await db_session.scalar(select(Subscription))
    assert subscription is not None
    subscription.current_period_end = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.commit()
    service = LiveKitDispatchService(
        db_session,
        _ForbiddenDirectDispatch(),
        realtime_service=_Realtime(),
        recording_service=_Recording(),
    )

    result = await service.handle_participant_joined(_sip_join())

    assert result.status == "denied"
    assert await db_session.scalar(select(func.count()).select_from(Call)) == 0
    assert await db_session.scalar(select(func.count()).select_from(OutboxEvent)) == 0


@pytest.mark.anyio
async def test_only_expected_agent_identity_connects_and_starts_recording(db_session) -> None:
    user, _phone, _config = await _seed_eligible_user(db_session)
    recording = _Recording()
    service = LiveKitDispatchService(
        db_session,
        _ForbiddenDirectDispatch(),
        realtime_service=_Realtime(),
        recording_service=recording,
    )
    await service.handle_participant_joined(_sip_join())
    call = await db_session.scalar(select(Call))
    assert call is not None

    wrong = await service.handle_participant_joined(
        {
            "event": "participant_joined",
            "room": {"name": "room-1"},
            "participant": {"identity": "agent-wrong", "kind": "AGENT", "attributes": {}},
        }
    )
    accepted = await service.handle_participant_joined(
        {
            "event": "participant_joined",
            "room": {"name": "room-1"},
            "participant": {
                "identity": f"agent-call-{call.id}",
                "kind": 4,
                "attributes": {},
            },
        }
    )

    await db_session.refresh(call)
    assert wrong.status == "ignored"
    assert accepted.status == "connected"
    assert call.status == "connected"
    assert call.started_at is not None
    assert call.recording_egress_id == "egress-1"
    assert recording.starts == [
        {"room_name": "room-1", "user_id": user.id, "call_id": call.id}
    ]


@pytest.mark.anyio
async def test_agent_join_commits_connected_state_before_recording_io_and_then_persists(
    db_session,
) -> None:
    await _seed_eligible_user(db_session)
    recording = _CommitAwareRecording(db_session)
    service = LiveKitDispatchService(
        db_session,
        _ForbiddenDirectDispatch(),
        realtime_service=_Realtime(),
        recording_service=recording,
    )
    await service.handle_participant_joined(_sip_join())
    call = await db_session.scalar(select(Call))
    assert call is not None

    result = await service.handle_participant_joined(
        {
            "event": "participant_joined",
            "room": {"name": "room-1"},
            "participant": {
                "identity": f"agent-call-{call.id}",
                "kind": "AGENT",
                "attributes": {},
            },
        }
    )

    await db_session.refresh(call)
    assert result.status == "connected"
    assert call.status == "connected"
    assert call.recording_egress_id == "egress-1"


@pytest.mark.anyio
async def test_recording_metadata_is_not_orphaned_when_completion_races_provider_success(
    db_session,
) -> None:
    await _seed_eligible_user(db_session)
    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    recording = _CompletingRecording(session_factory)
    service = LiveKitDispatchService(
        db_session,
        _ForbiddenDirectDispatch(),
        realtime_service=_Realtime(),
        recording_service=recording,
    )
    await service.handle_participant_joined(_sip_join())
    call = await db_session.scalar(select(Call))
    assert call is not None

    result = await service.handle_participant_joined(
        {
            "event": "participant_joined",
            "room": {"name": "room-1"},
            "participant": {
                "identity": f"agent-call-{call.id}",
                "kind": "AGENT",
                "attributes": {},
            },
        }
    )

    await db_session.refresh(call)
    assert result.status == "connected"
    assert call.status == "completed"
    assert call.recording_egress_id is None
    assert recording.stops == ["egress-1"]


@pytest.mark.anyio
async def test_failed_immediate_orphan_cleanup_persists_reference_only_retry(
    db_session,
) -> None:
    await _seed_eligible_user(db_session)
    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    recording = _FailingCleanupRecording(session_factory)
    service = LiveKitDispatchService(
        db_session,
        _ForbiddenDirectDispatch(),
        realtime_service=_Realtime(),
        recording_service=recording,
    )
    await service.handle_participant_joined(_sip_join())
    call = await db_session.scalar(select(Call))
    assert call is not None
    call_id = call.id

    result = await service.handle_participant_joined(
        {
            "event": "participant_joined",
            "room": {"name": "room-1"},
            "participant": {
                "identity": f"agent-call-{call_id}",
                "kind": "AGENT",
                "attributes": {},
            },
        }
    )

    db_session.expire_all()
    stored = await db_session.get(Call, call_id)
    assert result.status == "connected"
    assert stored.status == "completed"
    assert stored.ended_at.replace(tzinfo=UTC) == datetime(
        2026, 7, 13, 12, 0, tzinfo=UTC
    )
    assert stored.duration_seconds == 17
    assert stored.minutes_charged == 1
    assert stored.recording_egress_id == "egress-1"
    assert stored.recording_object_key.endswith(f"/{call_id}.ogg")
    intent = await db_session.scalar(
        select(OutboxEvent).where(
            OutboxEvent.topic == "recording.stop",
            OutboxEvent.aggregate_id == call_id,
        )
    )
    assert intent is not None
    assert intent.aggregate_type == "call-recording"
    assert intent.payload == {"call_id": str(call_id)}

    class RetryRecordingProvider:
        def __init__(self) -> None:
            self.stops: list[str] = []

        async def ensure_stopped(self, egress_id: str) -> None:
            self.stops.append(egress_id)

    retry_provider = RetryRecordingProvider()
    await deliver_recording_stop(
        {
            "session_factory": session_factory,
            "livekit_recording_provider": retry_provider,
        },
        intent,
    )
    assert retry_provider.stops == ["egress-1"]
