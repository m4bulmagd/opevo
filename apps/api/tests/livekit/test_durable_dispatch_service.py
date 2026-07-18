from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.agent_config import AgentConfig
from app.models.business_profile import BusinessProfile
from app.models.call import Call
from app.models.customer_activation import CustomerActivation
from app.models.outbox_event import OutboxEvent
from app.models.phone_number import PhoneNumber
from app.models.subscription import Subscription
from app.models.usage_ledger import UsageLedger
from app.schemas.agent_content import (
    AGENT_NAME_MAX_LENGTH,
    KNOWLEDGE_BASE_MAX_LENGTH,
    OWNER_CONTEXT_MAX_LENGTH,
    OWNER_NAME_MAX_LENGTH,
    SYSTEM_PROMPT_MAX_LENGTH,
)
from app.schemas.livekit import LiveKitDispatchMetadata
from app.schemas.business_profile import WEEKDAYS
from app.services.routing_fingerprint import routing_fingerprint
from app.services.livekit_dispatch_service import LiveKitDispatchService
from app.workers.jobs.outbox_topics import deliver_recording_stop


@pytest.fixture(autouse=True)
def _activation_flow_defaults_off_for_legacy_dispatch_tests(
    monkeypatch: pytest.MonkeyPatch,
):
    from app.core.config import get_settings

    monkeypatch.setenv("ACTIVATION_FLOW_ENABLED", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


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


class _FailingRealtime:
    async def publish_call_started(
        self,
        user_id: str,
        *,
        room_name: str,
        call_id: str,
    ) -> None:
        raise RuntimeError("REALTIME_PROVIDER_SECRET caller transcript")


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
        self.jobs: list[tuple[str, dict, dict]] = []

    async def enqueue_job(self, name: str, payload: dict, **kwargs) -> None:
        self.jobs.append((name, payload, kwargs))


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


async def _seed_verified_activation(
    db_session,
    *,
    user,
    phone: PhoneNumber,
    config: AgentConfig,
    active: bool,
) -> tuple[BusinessProfile, CustomerActivation]:
    now = datetime.now(UTC)
    profile = BusinessProfile(
        user_id=user.id,
        owner_name="Camille Martin",
        business_name="Atelier Martin",
        business_type="Plomberie",
        public_description="Dépannage et installation de plomberie.",
        timezone="Europe/Paris",
        business_hours={
            day: {"closed": True, "intervals": []} for day in WEEKDAYS
        },
        existing_phone_e164="+33199000200",
        confirmed_carrier="orange",
        receptionist_name="Léa",
        content_revision=3,
        routing_revision=2,
    )
    activation = CustomerActivation(
        user_id=user.id,
        profile_confirmed_revision=profile.content_revision,
        profile_confirmed_at=now - timedelta(hours=2),
        provisioning_consented_at=now - timedelta(hours=1),
        verification_status="succeeded",
        forwarding_verified_at=now - timedelta(minutes=30),
        go_live_requested_at=now - timedelta(minutes=2) if active else None,
        go_live_approved_at=now - timedelta(minutes=2) if active else None,
        activated_at=now - timedelta(minutes=1) if active else None,
    )
    config.profile_projection_revision = profile.content_revision
    db_session.add_all([profile, activation])
    await db_session.flush()
    activation.verified_routing_fingerprint = routing_fingerprint(profile, phone)
    await db_session.commit()
    return profile, activation


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
async def test_activation_flow_denies_before_go_live_and_admits_after_provider_success(
    db_session,
    monkeypatch,
) -> None:
    from app.core.config import get_settings

    monkeypatch.setenv("ACTIVATION_FLOW_ENABLED", "true")
    get_settings.cache_clear()
    try:
        user, phone, config = await _seed_eligible_user(db_session)
        _profile, activation = await _seed_verified_activation(
            db_session,
            user=user,
            phone=phone,
            config=config,
            active=False,
        )
        service = LiveKitDispatchService(
            db_session,
            _ForbiddenDirectDispatch(),
            realtime_service=None,
            recording_service=_Recording(),
        )

        denied = await service.handle_participant_joined(
            _sip_join(room="room-before-go-live")
        )
        assert denied.status == "denied"
        assert await db_session.scalar(select(func.count()).select_from(Call)) == 0
        assert await db_session.scalar(select(func.count()).select_from(OutboxEvent)) == 0

        now = datetime.now(UTC)
        activation.go_live_requested_at = now - timedelta(minutes=2)
        activation.go_live_approved_at = now - timedelta(minutes=2)
        activation.activated_at = now - timedelta(minutes=1)
        await db_session.commit()
        accepted = await service.handle_participant_joined(
            _sip_join(room="room-after-go-live")
        )

        assert accepted.status == "accepted"
        assert await db_session.scalar(select(func.count()).select_from(Call)) == 1
        assert await db_session.scalar(select(func.count()).select_from(OutboxEvent)) == 1
    finally:
        get_settings.cache_clear()


@pytest.mark.anyio
async def test_livekit_outbox_rechecks_current_activation_prerequisites(
    db_session,
    monkeypatch,
) -> None:
    from app.core.config import get_settings
    from app.workers.jobs.outbox_delivery import OutboxDeliveryError
    from app.workers.jobs.outbox_topics import _dispatch_snapshot

    monkeypatch.setenv("ACTIVATION_FLOW_ENABLED", "true")
    get_settings.cache_clear()
    try:
        user, phone, config = await _seed_eligible_user(db_session)
        profile, _activation = await _seed_verified_activation(
            db_session,
            user=user,
            phone=phone,
            config=config,
            active=True,
        )
        service = LiveKitDispatchService(
            db_session,
            _ForbiddenDirectDispatch(),
            realtime_service=None,
            recording_service=_Recording(),
        )
        accepted = await service.handle_participant_joined(
            _sip_join(room="room-stale-livekit-dispatch")
        )
        assert accepted.status == "accepted"
        call = (await db_session.scalars(select(Call))).one()
        call_id = call.id

        profile.existing_phone_e164 = "+33199000201"
        profile.routing_revision += 1
        await db_session.commit()
        session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

        with pytest.raises(OutboxDeliveryError) as error:
            await _dispatch_snapshot(session_factory, call_id)

        assert error.value.error_code == "dispatch_ineligible"
    finally:
        get_settings.cache_clear()


@pytest.mark.anyio
async def test_disabled_activation_flow_preserves_legacy_dispatch(
    db_session,
    monkeypatch,
) -> None:
    from app.core.config import get_settings

    monkeypatch.setenv("ACTIVATION_FLOW_ENABLED", "false")
    get_settings.cache_clear()
    try:
        await _seed_eligible_user(db_session)
        service = LiveKitDispatchService(
            db_session,
            _ForbiddenDirectDispatch(),
            realtime_service=None,
            recording_service=_Recording(),
        )

        result = await service.handle_participant_joined(
            _sip_join(room="room-legacy-activation-off")
        )

        assert result.status == "accepted"
    finally:
        get_settings.cache_clear()


def _dispatch_metadata_payload(**overrides) -> dict:
    defaults = {
        "call_id": "call-1",
        "user_id": "user-1",
        "agent_config_id": "config-1",
        "agent_identity": "agent-call-1",
        "agent_name": "Ava",
        "owner_name": "Sam",
        "owner_context": "Dental reception",
        "system_prompt": "Handle calls professionally.",
        "knowledge_base": "Open weekdays.",
        "pipeline_mode": "stt_llm_tts",
        "minutes_remaining": 60,
        "allowed_duration_seconds": 3600,
        "dispatch_token": "dispatch-token",
    }
    defaults.update(overrides)
    return defaults


@pytest.mark.parametrize(
    ("field_name", "maximum"),
    [
        ("agent_name", AGENT_NAME_MAX_LENGTH),
        ("owner_name", OWNER_NAME_MAX_LENGTH),
        ("owner_context", OWNER_CONTEXT_MAX_LENGTH),
        ("system_prompt", SYSTEM_PROMPT_MAX_LENGTH),
        ("knowledge_base", KNOWLEDGE_BASE_MAX_LENGTH),
    ],
)
def test_api_dispatch_metadata_normalizes_and_bounds_customer_content(
    field_name: str,
    maximum: int,
) -> None:
    bounded_value = "x" * maximum
    metadata = LiveKitDispatchMetadata.model_validate(
        _dispatch_metadata_payload(**{field_name: f"  {bounded_value}  "})
    )

    assert getattr(metadata, field_name) == bounded_value
    with pytest.raises(ValidationError):
        LiveKitDispatchMetadata.model_validate(
            _dispatch_metadata_payload(**{field_name: "x" * (maximum + 1)})
        )


def test_api_dispatch_metadata_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        LiveKitDispatchMetadata.model_validate(
            _dispatch_metadata_payload(untrusted_extra="value")
        )


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
    assert pool.jobs == [("outbox_delivery_job", {}, {})]
    assert realtime.events == [
        {
            "user_id": str(user.id),
            "room_name": "room-1",
            "call_id": str(calls[0].id),
        }
    ]


@pytest.mark.anyio
async def test_sip_join_is_durable_without_realtime_service(db_session) -> None:
    await _seed_eligible_user(db_session)
    pool = _Pool()
    service = LiveKitDispatchService(
        db_session,
        _ForbiddenDirectDispatch(),
        realtime_service=None,
        recording_service=_Recording(),
        arq_pool=pool,
    )

    result = await service.handle_participant_joined(
        _sip_join(room="room-no-realtime")
    )

    calls = list((await db_session.execute(select(Call))).scalars())
    events = list((await db_session.execute(select(OutboxEvent))).scalars())
    assert result.status == "accepted"
    assert len(calls) == len(events) == 1
    assert calls[0].livekit_room_id == "room-no-realtime"
    assert events[0].aggregate_id == calls[0].id
    assert pool.jobs == [("outbox_delivery_job", {}, {})]


@pytest.mark.anyio
async def test_realtime_publish_failure_cannot_change_durable_acceptance(
    db_session,
    caplog,
) -> None:
    await _seed_eligible_user(db_session)
    service = LiveKitDispatchService(
        db_session,
        _ForbiddenDirectDispatch(),
        realtime_service=_FailingRealtime(),
        recording_service=_Recording(),
    )

    with caplog.at_level("WARNING"):
        result = await service.handle_participant_joined(
            _sip_join(room="room-failing-realtime")
        )

    calls = list((await db_session.execute(select(Call))).scalars())
    events = list((await db_session.execute(select(OutboxEvent))).scalars())
    assert result.status == "accepted"
    assert len(calls) == len(events) == 1
    assert events[0].aggregate_id == calls[0].id
    assert "event=livekit_realtime_publish_failed" in caplog.text
    assert "REALTIME_PROVIDER_SECRET" not in caplog.text
    assert "caller transcript" not in caplog.text


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
@pytest.mark.parametrize(
    "ineligible_case",
    [
        "zero_balance",
        "missing_period",
        "expired_period",
        "unsupported_plan",
        "incomplete_agent",
        "oversized_agent_content",
        "disabled_agent",
        "inactive_phone",
        "inactive_phone_projection",
        "missing_provider_id",
        "called_number_mismatch",
    ],
)
async def test_readiness_blocker_creates_no_call_or_outbox(
    db_session,
    ineligible_case: str,
) -> None:
    await _seed_eligible_user(db_session)
    subscription = await db_session.scalar(select(Subscription))
    config = await db_session.scalar(select(AgentConfig))
    phone = await db_session.scalar(select(PhoneNumber))
    usage = await db_session.scalar(select(UsageLedger))
    assert subscription is not None
    assert config is not None
    assert phone is not None
    assert usage is not None

    if ineligible_case == "zero_balance":
        usage.balance_after = 0
    elif ineligible_case == "missing_period":
        subscription.current_period_start = None
    elif ineligible_case == "expired_period":
        subscription.current_period_end = datetime.now(UTC) - timedelta(seconds=1)
    elif ineligible_case == "unsupported_plan":
        await db_session.execute(text("PRAGMA ignore_check_constraints = ON"))
        subscription.plan_tier = "enterprise"
    elif ineligible_case == "incomplete_agent":
        config.owner_context = ""
    elif ineligible_case == "oversized_agent_content":
        config.agent_name = "A" * (AGENT_NAME_MAX_LENGTH + 1)
    elif ineligible_case == "disabled_agent":
        config.is_enabled = False
    elif ineligible_case == "inactive_phone":
        phone.is_active = False
    elif ineligible_case == "inactive_phone_projection":
        phone.provider_connection_name = "app-disabled"
    elif ineligible_case == "missing_provider_id":
        phone.provider_number_id = None

    await db_session.commit()
    service = LiveKitDispatchService(
        db_session,
        _ForbiddenDirectDispatch(),
        realtime_service=_Realtime(),
        recording_service=_Recording(),
    )

    result = await service.handle_participant_joined(
        _sip_join(
            trunk=(
                "+35315550000"
                if ineligible_case == "called_number_mismatch"
                else "+33999888777"
            )
        )
    )

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
        realtime_service=None,
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
async def test_sip_leave_commits_and_enqueues_finalization_without_realtime(
    db_session,
) -> None:
    await _seed_eligible_user(db_session)
    pool = _Pool()
    service = LiveKitDispatchService(
        db_session,
        _ForbiddenDirectDispatch(),
        realtime_service=None,
        recording_service=_Recording(),
        arq_pool=pool,
    )
    joined = await service.handle_participant_joined(_sip_join())
    assert joined.call_id is not None
    connected = await service.handle_participant_joined(
        {
            "event": "participant_joined",
            "room": {"name": "room-1"},
            "participant": {
                "identity": f"agent-call-{joined.call_id}",
                "kind": "AGENT",
                "attributes": {},
            },
        }
    )

    left = await service.handle_participant_left(
        {
            "event": "participant_left",
            "room": {"name": "room-1"},
            "participant": {
                "identity": "caller",
                "kind": "SIP",
                "attributes": {},
            },
        }
    )

    call = await db_session.get(Call, UUID(joined.call_id))
    assert connected.status == "connected"
    assert left.status == "ending"
    assert call is not None
    assert call.status == "ending"
    assert call.ended_at is not None
    assert pool.jobs == [
        ("outbox_delivery_job", {}, {}),
        (
            "call_finalization_job",
            {"call_id": str(call.id)},
            {"_job_id": f"call-finalization:{call.id}"},
        ),
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
