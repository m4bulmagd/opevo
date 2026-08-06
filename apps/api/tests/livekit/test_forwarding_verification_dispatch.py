from datetime import UTC, datetime, timedelta
import json
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.activation_event import ActivationEvent
from app.models.agent_config import AgentConfig
from app.models.business_profile import BusinessProfile
from app.models.call import Call
from app.models.call_message import CallMessage
from app.models.customer_activation import CustomerActivation
from app.models.notification import Notification
from app.models.outbox_event import OutboxEvent
from app.models.phone_number import PhoneNumber
from app.models.subscription import Subscription
from app.models.usage_ledger import UsageLedger
from app.models.user import User
from app.models.webhook_event import WebhookEvent
from app.services.forwarding_verification_service import ForwardingVerificationService
from app.services.inbound_verification_service import InboundVerificationService
from app.services.livekit_dispatch_service import LiveKitDispatchService
from app.services.outbox_service import OutboxService


FIXED_NOW = datetime(2026, 7, 18, 10, 0, tzinfo=UTC)
SOURCE_NUMBER = "+33199000000"
PRESVO_NUMBER = "+33999000000"
ALTERNATE_SOURCE_NUMBER = "+33199000001"
ALTERNATE_PRESVO_NUMBER = "+33999000001"


@pytest.fixture
def activation_flow_disabled(monkeypatch: pytest.MonkeyPatch):
    from app.core.config import get_settings

    monkeypatch.setenv("ACTIVATION_FLOW_ENABLED", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class _Realtime:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def publish_call_started(
        self,
        user_id: UUID,
        *,
        room_name: str,
        call_id: UUID,
    ) -> None:
        self.events.append(
            {"user_id": user_id, "room_name": room_name, "call_id": call_id}
        )


class _Recording:
    def __init__(self) -> None:
        self.starts: list[dict] = []

    async def start_room_recording(self, **kwargs) -> None:
        self.starts.append(kwargs)


async def _seed_open_window(db_session, active_user) -> CustomerActivation:
    profile = BusinessProfile(
        user_id=active_user.id,
        owner_name="Camille Martin",
        business_name="Atelier Martin",
        business_type="Plomberie",
        public_description="Dépannage et installation de plomberie.",
        timezone="Europe/Paris",
        business_hours={"monday": {"closed": False, "intervals": []}},
        existing_phone_e164=SOURCE_NUMBER,
        confirmed_carrier="orange",
        receptionist_name="Léa",
        content_revision=3,
        routing_revision=2,
    )
    activation = CustomerActivation(
        user_id=active_user.id,
        profile_confirmed_revision=3,
        profile_confirmed_at=FIXED_NOW - timedelta(hours=1),
        verification_window_started_at=FIXED_NOW - timedelta(minutes=1),
        verification_window_expires_at=FIXED_NOW + timedelta(minutes=9),
        verification_status="open",
    )
    phone = PhoneNumber(
        user_id=active_user.id,
        e164=PRESVO_NUMBER,
        country_code="FR",
        provider="fake",
        provider_number_id="fake_verification_number",
        provider_connection_name="app-disabled",
        is_active=False,
    )
    db_session.add_all([profile, activation, phone])
    await db_session.commit()
    return activation


def _sip_join(*, attributes: dict[str, object] | None = None) -> dict:
    return {
        "id": "verification-webhook-1",
        "event": "participant_joined",
        "room": {"name": "verification-room-1"},
        "participant": {
            "identity": "sip-caller",
            "kind": "SIP",
            "attributes": (
                attributes
                if attributes is not None
                else {
                    "sip.phoneNumber": SOURCE_NUMBER,
                    "sip.trunkPhoneNumber": PRESVO_NUMBER,
                    "sip.diversion": SOURCE_NUMBER,
                }
            ),
        },
    }


async def _seed_normal_dispatch_state(db_session, active_user) -> None:
    now = FIXED_NOW
    db_session.add_all(
        [
            PhoneNumber(
                user_id=active_user.id,
                e164=PRESVO_NUMBER,
                country_code="FR",
                provider="fake",
                provider_number_id="fake_normal_number",
                provider_connection_name="app-active",
                is_active=True,
            ),
            AgentConfig(
                user_id=active_user.id,
                agent_name="Ava",
                owner_context="Atelier Martin",
                system_prompt="Be helpful",
                knowledge_base="Hours 9-5",
                pipeline_mode="stt_llm_tts",
                is_enabled=True,
            ),
            Subscription(
                user_id=active_user.id,
                stripe_customer_id="cus-fictional-normal",
                stripe_subscription_id="sub-fictional-normal",
                plan_tier="starter",
                status="active",
                allocated_minutes=60,
                current_period_start=now - timedelta(days=1),
                current_period_end=now + timedelta(days=1),
            ),
            UsageLedger(
                user_id=active_user.id,
                event_type="invoice_paid_reset",
                source_id="invoice-fictional-normal",
                minutes_delta=60,
                balance_after=60,
            ),
        ]
    )
    await db_session.commit()


@pytest.mark.anyio
async def test_open_window_is_claimed_before_normal_call_admission(
    db_session,
    active_user,
) -> None:
    activation = await _seed_open_window(db_session, active_user)
    realtime = _Realtime()
    recording = _Recording()
    service = LiveKitDispatchService(
        db_session,
        activation_flow_enabled=True,
        realtime_service=realtime,
        recording_service=recording,
        now_provider=lambda: FIXED_NOW,
    )

    result = await service.handle_participant_joined(_sip_join())

    await db_session.refresh(activation)
    outbox = list((await db_session.scalars(select(OutboxEvent))).all())
    assert result.status == "verification_claimed"
    assert activation.verification_status == "claimed"
    assert activation.verification_session_id is not None
    assert len(outbox) == 1
    assert outbox[0].topic == "livekit.verification_dispatch"
    assert outbox[0].aggregate_type == "forwarding-verification"
    assert outbox[0].aggregate_id == activation.id
    assert outbox[0].idempotency_key == (
        f"livekit.verification_dispatch:{activation.verification_session_id}"
    )
    assert outbox[0].payload == {
        "activation_id": str(activation.id),
        "session_id": activation.verification_session_id,
        "room_name": "verification-room-1",
        "lifecycle_generation": active_user.lifecycle_generation,
    }
    assert await db_session.scalar(select(func.count()).select_from(Call)) == 0
    assert await db_session.scalar(select(func.count()).select_from(CallMessage)) == 0
    assert await db_session.scalar(select(func.count()).select_from(Notification)) == 0
    assert await db_session.scalar(select(func.count()).select_from(UsageLedger)) == 0
    assert (
        await db_session.scalar(select(func.count()).select_from(ActivationEvent)) == 1
    )
    assert realtime.events == []
    assert recording.starts == []


@pytest.mark.anyio
async def test_present_mismatched_diversion_does_not_claim(
    db_session,
    active_user,
) -> None:
    activation = await _seed_open_window(db_session, active_user)
    service = LiveKitDispatchService(
        db_session,
        activation_flow_enabled=True,
        realtime_service=_Realtime(),
        recording_service=_Recording(),
        now_provider=lambda: FIXED_NOW,
    )

    result = await service.handle_participant_joined(
        _sip_join(
            attributes={
                "sip.phoneNumber": SOURCE_NUMBER,
                "sip.trunkPhoneNumber": PRESVO_NUMBER,
                "sip.diversion": ALTERNATE_SOURCE_NUMBER,
            }
        )
    )

    await db_session.refresh(activation)
    assert result.status == "denied"
    assert activation.verification_status == "open"
    assert activation.verification_session_id is None
    assert (
        await db_session.scalar(select(func.count()).select_from(ActivationEvent)) == 0
    )
    assert await db_session.scalar(select(func.count()).select_from(OutboxEvent)) == 0
    assert await db_session.scalar(select(func.count()).select_from(Call)) == 0


@pytest.mark.anyio
@pytest.mark.parametrize("malformed_diversion", [{}, [], 123, True])
async def test_malformed_diversion_value_does_not_escape_verification_admission(
    db_session,
    active_user,
    malformed_diversion: object,
) -> None:
    activation = await _seed_open_window(db_session, active_user)
    service = LiveKitDispatchService(
        db_session,
        activation_flow_enabled=True,
        realtime_service=_Realtime(),
        recording_service=_Recording(),
        now_provider=lambda: FIXED_NOW,
    )

    result = await service.handle_participant_joined(
        _sip_join(
            attributes={
                "sip.phoneNumber": SOURCE_NUMBER,
                "sip.trunkPhoneNumber": PRESVO_NUMBER,
                "sip.diversion": malformed_diversion,
            }
        )
    )

    await db_session.refresh(activation)
    assert result.status == "denied"
    assert activation.verification_status == "open"
    assert activation.verification_session_id is None
    assert await db_session.scalar(select(func.count()).select_from(Call)) == 0
    assert await db_session.scalar(select(func.count()).select_from(OutboxEvent)) == 0


@pytest.mark.anyio
async def test_missing_diversion_is_allowed(db_session, active_user) -> None:
    activation = await _seed_open_window(db_session, active_user)
    service = LiveKitDispatchService(
        db_session,
        activation_flow_enabled=True,
        realtime_service=_Realtime(),
        recording_service=_Recording(),
        now_provider=lambda: FIXED_NOW,
    )

    result = await service.handle_participant_joined(
        _sip_join(
            attributes={
                "sip.phoneNumber": SOURCE_NUMBER,
                "sip.trunkPhoneNumber": PRESVO_NUMBER,
            }
        )
    )

    await db_session.refresh(activation)
    assert result.status == "verification_claimed"
    assert activation.verification_status == "claimed"


@pytest.mark.anyio
async def test_no_window_continues_unchanged_normal_dispatch(
    db_session,
    active_user,
    activation_flow_disabled,
) -> None:
    await _seed_normal_dispatch_state(db_session, active_user)
    realtime = _Realtime()
    service = LiveKitDispatchService(
        db_session,
        activation_flow_enabled=False,
        realtime_service=realtime,
        recording_service=_Recording(),
        now_provider=lambda: FIXED_NOW,
    )

    result = await service.handle_participant_joined(_sip_join())

    call = await db_session.scalar(select(Call))
    outbox = await db_session.scalar(select(OutboxEvent))
    assert result.status == "accepted"
    assert call is not None
    assert outbox is not None
    assert outbox.topic == "livekit.dispatch"
    assert outbox.aggregate_type == "call"
    assert outbox.aggregate_id == call.id
    assert outbox.payload == {
        "call_id": str(call.id),
        "lifecycle_generation": active_user.lifecycle_generation,
    }
    assert len(realtime.events) == 1


@pytest.mark.anyio
async def test_deactivation_commit_prevents_later_customer_call_admission(
    db_session,
    active_user,
    activation_flow_disabled,
) -> None:
    await _seed_normal_dispatch_state(db_session, active_user)
    active_user.status = "deactivating"
    await db_session.commit()
    service = LiveKitDispatchService(
        db_session,
        activation_flow_enabled=False,
        realtime_service=_Realtime(),
        recording_service=_Recording(),
        now_provider=lambda: FIXED_NOW,
    )

    result = await service.handle_participant_joined(_sip_join())

    assert result.status == "denied"
    assert await db_session.scalar(select(func.count(Call.id))) == 0
    assert await db_session.scalar(select(func.count(OutboxEvent.id))) == 0


@pytest.mark.anyio
@pytest.mark.parametrize(
    "called_number",
    ["not-a-number", ALTERNATE_PRESVO_NUMBER],
)
async def test_malformed_or_wrong_called_number_does_not_claim(
    db_session,
    active_user,
    called_number: str,
) -> None:
    activation = await _seed_open_window(db_session, active_user)
    service = LiveKitDispatchService(
        db_session,
        activation_flow_enabled=True,
        realtime_service=_Realtime(),
        recording_service=_Recording(),
        now_provider=lambda: FIXED_NOW,
    )

    result = await service.handle_participant_joined(
        _sip_join(
            attributes={
                "sip.phoneNumber": SOURCE_NUMBER,
                "sip.trunkPhoneNumber": called_number,
                "sip.diversion": SOURCE_NUMBER,
            }
        )
    )

    await db_session.refresh(activation)
    assert result.status == "denied"
    assert activation.verification_status == "open"
    assert activation.verification_session_id is None
    assert (
        await db_session.scalar(select(func.count()).select_from(ActivationEvent)) == 0
    )
    assert await db_session.scalar(select(func.count()).select_from(OutboxEvent)) == 0


@pytest.mark.anyio
async def test_expired_window_does_not_claim(db_session, active_user) -> None:
    activation = await _seed_open_window(db_session, active_user)
    activation.verification_window_expires_at = FIXED_NOW
    await db_session.commit()
    service = LiveKitDispatchService(
        db_session,
        activation_flow_enabled=True,
        realtime_service=_Realtime(),
        recording_service=_Recording(),
        now_provider=lambda: FIXED_NOW,
    )

    result = await service.handle_participant_joined(_sip_join())

    await db_session.refresh(activation)
    assert result.status == "denied"
    assert activation.verification_status == "open"
    assert activation.verification_session_id is None
    assert (
        await db_session.scalar(select(func.count()).select_from(ActivationEvent)) == 0
    )
    assert await db_session.scalar(select(func.count()).select_from(OutboxEvent)) == 0


@pytest.mark.anyio
async def test_outbox_failure_rolls_back_claim_and_audit(
    db_session,
    active_user,
) -> None:
    activation = await _seed_open_window(db_session, active_user)

    class FailingOutbox:
        async def add(self, **kwargs):
            await OutboxService(db_session).add(**kwargs)
            raise RuntimeError("forced outbox failure")

    inbound = InboundVerificationService(
        db_session,
        forwarding_verification_service=ForwardingVerificationService(
            db_session,
            now_provider=lambda: FIXED_NOW,
        ),
        outbox_service=FailingOutbox(),
    )
    service = LiveKitDispatchService(
        db_session,
        activation_flow_enabled=True,
        inbound_verification_service=inbound,
        realtime_service=_Realtime(),
        recording_service=_Recording(),
        now_provider=lambda: FIXED_NOW,
    )

    with pytest.raises(RuntimeError, match="forced outbox failure"):
        await service.handle_participant_joined(_sip_join())

    await db_session.refresh(activation)
    assert activation.verification_status == "open"
    assert activation.verification_session_id is None
    assert activation.verification_claimed_at is None
    assert activation.verification_routing_fingerprint is None
    assert (
        await db_session.scalar(select(func.count()).select_from(ActivationEvent)) == 0
    )
    assert await db_session.scalar(select(func.count()).select_from(OutboxEvent)) == 0


class _VerificationReceiver:
    def receive(self, _body: bytes, _authorization: str | None) -> dict:
        return _sip_join()


@pytest.mark.anyio
async def test_duplicate_webhook_id_claims_and_dispatches_exactly_once(
    async_client,
    test_app,
    client_database_url: str,
    configured_livekit_recording_runtime,
) -> None:
    now = datetime.now(UTC)
    engine = create_async_engine(client_database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        user = User(
            clerk_user_id="verification-webhook-owner",
            email="verification-webhook-owner@example.invalid",
        )
        session.add(user)
        await session.flush()
        session.add_all(
            [
                BusinessProfile(
                    user_id=user.id,
                    owner_name="Camille Martin",
                    business_name="Atelier Martin",
                    business_type="Plomberie",
                    public_description="Dépannage et installation de plomberie.",
                    timezone="Europe/Paris",
                    business_hours={"monday": {"closed": False, "intervals": []}},
                    existing_phone_e164=SOURCE_NUMBER,
                    confirmed_carrier="orange",
                    receptionist_name="Léa",
                    content_revision=3,
                    routing_revision=2,
                ),
                CustomerActivation(
                    user_id=user.id,
                    profile_confirmed_revision=3,
                    profile_confirmed_at=now - timedelta(hours=1),
                    verification_window_started_at=now - timedelta(minutes=1),
                    verification_window_expires_at=now + timedelta(minutes=9),
                    verification_status="open",
                ),
                PhoneNumber(
                    user_id=user.id,
                    e164=PRESVO_NUMBER,
                    country_code="FR",
                    provider="fake",
                    provider_number_id="fake_webhook_number",
                    provider_connection_name="app-disabled",
                    is_active=False,
                ),
            ]
        )
        await session.commit()

    from app.webhooks.livekit import get_realtime_service, get_webhook_receiver

    realtime = _Realtime()
    test_app.dependency_overrides[get_webhook_receiver] = _VerificationReceiver
    test_app.dependency_overrides[get_realtime_service] = lambda: realtime
    try:
        first = await async_client.post(
            "/webhooks/livekit",
            content=json.dumps({"ignored": True}).encode(),
            headers={"authorization": "Bearer test"},
        )
        duplicate = await async_client.post(
            "/webhooks/livekit",
            content=json.dumps({"ignored": True}).encode(),
            headers={"authorization": "Bearer test"},
        )
    finally:
        test_app.dependency_overrides.pop(get_webhook_receiver, None)
        test_app.dependency_overrides.pop(get_realtime_service, None)

    async with session_factory() as session:
        activation = await session.scalar(select(CustomerActivation))
        assert activation is not None
        assert activation.verification_status == "claimed"
        assert await session.scalar(select(func.count()).select_from(WebhookEvent)) == 1
        assert (
            await session.scalar(select(func.count()).select_from(ActivationEvent)) == 1
        )
        assert await session.scalar(select(func.count()).select_from(OutboxEvent)) == 1
        assert await session.scalar(select(func.count()).select_from(Call)) == 0
    await engine.dispose()
    assert first.status_code == 202
    assert duplicate.status_code == 202
    assert realtime.events == []


@pytest.mark.anyio
async def test_same_room_redelivery_with_new_event_id_is_verification_idempotent(
    db_session,
    active_user,
) -> None:
    activation = await _seed_open_window(db_session, active_user)
    service = LiveKitDispatchService(
        db_session,
        activation_flow_enabled=True,
        realtime_service=_Realtime(),
        recording_service=_Recording(),
        now_provider=lambda: FIXED_NOW,
    )
    first_event = _sip_join()
    redelivery = _sip_join()
    redelivery["id"] = "verification-webhook-redelivery"

    first = await service.handle_participant_joined(first_event)
    first_session_id = activation.verification_session_id
    second = await service.handle_participant_joined(redelivery)

    await db_session.refresh(activation)
    assert first.status == second.status == "verification_claimed"
    assert activation.verification_session_id == first_session_id
    assert (
        await db_session.scalar(select(func.count()).select_from(ActivationEvent)) == 1
    )
    assert await db_session.scalar(select(func.count()).select_from(OutboxEvent)) == 1
    assert await db_session.scalar(select(func.count()).select_from(Call)) == 0


@pytest.mark.anyio
async def test_same_room_replay_after_success_never_becomes_a_customer_call(
    db_session,
    active_user,
) -> None:
    activation = await _seed_open_window(db_session, active_user)
    service = LiveKitDispatchService(
        db_session,
        activation_flow_enabled=True,
        realtime_service=_Realtime(),
        recording_service=_Recording(),
        now_provider=lambda: FIXED_NOW,
    )
    first = await service.handle_participant_joined(_sip_join())
    session_id = activation.verification_session_id
    assert first.status == "verification_claimed"
    assert session_id is not None

    await ForwardingVerificationService(
        db_session,
        now_provider=lambda: FIXED_NOW,
    ).complete(session_id=session_id)
    phone = await db_session.scalar(
        select(PhoneNumber).where(PhoneNumber.user_id == active_user.id)
    )
    assert phone is not None
    phone.is_active = True
    phone.provider_connection_name = "app-active"
    db_session.add_all(
        [
            AgentConfig(
                user_id=active_user.id,
                agent_name="Ava",
                owner_context="Atelier Martin",
                system_prompt="Be helpful",
                knowledge_base="Hours 9-5",
                pipeline_mode="stt_llm_tts",
                is_enabled=True,
            ),
            Subscription(
                user_id=active_user.id,
                stripe_customer_id="cus-fictional-replay",
                stripe_subscription_id="sub-fictional-replay",
                plan_tier="starter",
                status="active",
                allocated_minutes=60,
                current_period_start=FIXED_NOW - timedelta(days=1),
                current_period_end=FIXED_NOW + timedelta(days=1),
            ),
            UsageLedger(
                user_id=active_user.id,
                event_type="invoice_paid_reset",
                source_id="invoice-fictional-replay",
                minutes_delta=60,
                balance_after=60,
            ),
        ]
    )
    await db_session.commit()

    replay = _sip_join()
    replay["id"] = "verification-webhook-after-success"
    result = await service.handle_participant_joined(replay)

    await db_session.refresh(activation)
    assert result.status == "verification_claimed"
    assert activation.verification_status == "succeeded"
    assert activation.verification_session_id == session_id
    assert await db_session.scalar(select(func.count()).select_from(Call)) == 0
    outbox = list((await db_session.scalars(select(OutboxEvent))).all())
    assert len(outbox) == 1
    assert outbox[0].topic == "livekit.verification_dispatch"


@pytest.mark.anyio
async def test_same_room_redelivery_rejects_corrupt_claim_audit_identity(
    db_session,
    active_user,
) -> None:
    await _seed_open_window(db_session, active_user)
    service = LiveKitDispatchService(
        db_session,
        activation_flow_enabled=True,
        realtime_service=_Realtime(),
        recording_service=_Recording(),
        now_provider=lambda: FIXED_NOW,
    )
    await service.handle_participant_joined(_sip_join())
    claim_event = await db_session.scalar(select(ActivationEvent))
    assert claim_event is not None
    claim_event.event_type = "verification_window_opened"
    await db_session.commit()

    result = await service.handle_participant_joined(_sip_join())

    assert result.status == "denied"
    assert (
        await db_session.scalar(select(func.count()).select_from(ActivationEvent)) == 1
    )
    assert await db_session.scalar(select(func.count()).select_from(OutboxEvent)) == 1
    assert await db_session.scalar(select(func.count()).select_from(Call)) == 0
