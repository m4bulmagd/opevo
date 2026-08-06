import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.database import get_session
from app.core.config import Settings
from app.core.dispatch_token import create_dispatch_token
from tests.dispatch_token_config import TEST_DISPATCH_TOKEN_CONFIG
from tests.conftest import install_test_api_runtime
from app.models.activation_event import ActivationEvent
from app.models.business_profile import BusinessProfile
from app.models.call import Call
from app.models.call_message import CallMessage
from app.models.customer_activation import CustomerActivation
from app.models.notification import Notification
from app.models.outbox_event import OutboxEvent
from app.models.phone_number import PhoneNumber
from app.models.phone_number_provisioning import PhoneNumberProvisioning
from app.models.recording_egress_operation import RecordingEgressOperation
from app.models.usage_ledger import UsageLedger
from app.providers.livekit_dispatch.base import LiveKitDispatch
from app.services.forwarding_verification_service import ForwardingVerificationService
from app.services.livekit_dispatch_service import LiveKitDispatchService
from app.workers.outbox.delivery import deliver_outbox_batch
from app.workers.outbox.verification_dispatch import (
    deliver_livekit_verification_dispatch,
)


# ARCEP-reserved fictional ranges: 019900 for source lines and 099900 for
# technical/internal lines. They cannot identify a real French subscriber.
SOURCE_NUMBER = "+33199000000"
PRESVO_NUMBER = "+33999000000"
OWNER_SENTINEL = "Privacy Owner Sentinel"
BUSINESS_SENTINEL = "Privacy Business Sentinel"
DESCRIPTION_SENTINEL = "Privacy Description Sentinel"
RECEPTIONIST_SENTINEL = "Privacy Receptionist Sentinel"


@pytest.fixture(autouse=True)
def _activation_flow_enabled(monkeypatch: pytest.MonkeyPatch):
    from app.core.config import get_settings

    monkeypatch.setenv("ACTIVATION_FLOW_ENABLED", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@dataclass(frozen=True, slots=True)
class _PrivacyCounts:
    calls: int
    messages: int
    usage_ledgers: int
    notifications: int
    summary_events: int
    recording_operations: int
    recording_events: int
    activation_events: int


class _Realtime:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def publish_call_started(self, *args, **kwargs) -> None:
        self.events.append({"args": args, "kwargs": kwargs})


class _Recording:
    def __init__(self) -> None:
        self.starts: list[dict] = []

    async def start_room_recording(self, **kwargs) -> None:
        self.starts.append(kwargs)


class _DispatchProvider:
    def __init__(self) -> None:
        self.dispatches: list[LiveKitDispatch] = []
        self.create_calls: list[dict[str, str]] = []

    async def list_dispatches(self, *, room_name: str) -> list[LiveKitDispatch]:
        return [item for item in self.dispatches if item.room == room_name]

    async def create_dispatch(
        self,
        *,
        agent_name: str,
        room_name: str,
        metadata: str,
    ) -> LiveKitDispatch:
        self.create_calls.append(
            {
                "agent_name": agent_name,
                "room_name": room_name,
                "metadata": metadata,
            }
        )
        dispatch = LiveKitDispatch(
            id="privacy-verification-dispatch",
            agent_name=agent_name,
            room=room_name,
            metadata=metadata,
            state="active",
        )
        self.dispatches.append(dispatch)
        return dispatch


def _sip_join(*, event_id: str, room_name: str) -> dict:
    return {
        "id": event_id,
        "event": "participant_joined",
        "room": {"name": room_name},
        "participant": {
            "identity": "sip-caller",
            "kind": "SIP",
            "attributes": {
                "sip.phoneNumber": SOURCE_NUMBER,
                "sip.trunkPhoneNumber": PRESVO_NUMBER,
                "sip.diversion": SOURCE_NUMBER,
            },
        },
    }


async def _seed_provisioned_customer(
    db_session,
    user,
    *,
    now: datetime,
) -> tuple[CustomerActivation, PhoneNumber]:
    user.country_code = "FR"
    profile = BusinessProfile(
        user_id=user.id,
        owner_name=OWNER_SENTINEL,
        business_name=BUSINESS_SENTINEL,
        business_type="Plomberie",
        public_description=DESCRIPTION_SENTINEL,
        timezone="Europe/Paris",
        business_hours={"monday": {"closed": False, "intervals": []}},
        existing_phone_e164=SOURCE_NUMBER,
        confirmed_carrier="orange",
        receptionist_name=RECEPTIONIST_SENTINEL,
        content_revision=3,
        routing_revision=2,
    )
    activation = CustomerActivation(
        user_id=user.id,
        profile_confirmed_revision=3,
        profile_confirmed_at=now - timedelta(hours=1),
        provisioning_consented_at=now - timedelta(minutes=30),
    )
    phone = PhoneNumber(
        user_id=user.id,
        e164=PRESVO_NUMBER,
        country_code="FR",
        provider="fake",
        provider_number_id="fake-privacy-verification-number",
        provider_connection_name="app-disabled",
        is_active=False,
    )
    db_session.add_all([profile, activation, phone])
    await db_session.flush()
    db_session.add(
        PhoneNumberProvisioning(
            user_id=user.id,
            phone_number_id=phone.id,
            target_country_code="FR",
            status="succeeded",
            attempt_count=1,
            can_retry=False,
            provider_operation_key=f"privacy:phone.provision:{activation.id}",
        )
    )
    await db_session.commit()
    return activation, phone


async def _privacy_counts(db_session) -> _PrivacyCounts:
    async def count(model, *conditions) -> int:
        statement = select(func.count()).select_from(model)
        if conditions:
            statement = statement.where(*conditions)
        return int(await db_session.scalar(statement) or 0)

    return _PrivacyCounts(
        calls=await count(Call),
        messages=await count(CallMessage),
        usage_ledgers=await count(UsageLedger),
        notifications=await count(Notification),
        summary_events=await count(
            OutboxEvent,
            OutboxEvent.topic == "summary.generate",
        ),
        recording_operations=await count(RecordingEgressOperation),
        recording_events=await count(
            OutboxEvent,
            OutboxEvent.topic == "recording.reconcile",
        ),
        activation_events=await count(ActivationEvent),
    )


@pytest.mark.anyio
async def test_verification_lifecycle_is_private_and_runtime_isolated(
    db_session,
    active_user,
    caplog: pytest.LogCaptureFixture,
) -> None:
    now = datetime.now(UTC)
    activation, phone = await _seed_provisioned_customer(
        db_session,
        active_user,
        now=now,
    )
    activation_id = activation.id
    phone_id = phone.id
    user_id = active_user.id
    before = await _privacy_counts(db_session)
    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    realtime = _Realtime()
    recording = _Recording()
    provider = _DispatchProvider()

    with caplog.at_level(logging.INFO):
        await ForwardingVerificationService(
            db_session,
            now_provider=lambda: now,
        ).open_window(user_id)
        claim_result = await LiveKitDispatchService(
            db_session,
            activation_flow_enabled=True,
            realtime_service=realtime,
            recording_service=recording,
            now_provider=lambda: now + timedelta(minutes=1),
        ).handle_participant_joined(
            _sip_join(
                event_id="privacy-verification-claim",
                room_name="privacy-verification-room",
            )
        )

        db_session.expire_all()
        claimed_activation = await db_session.get(CustomerActivation, activation_id)
        verification_event = await db_session.scalar(
            select(OutboxEvent).where(
                OutboxEvent.topic == "livekit.verification_dispatch"
            )
        )
        assert claimed_activation is not None
        assert verification_event is not None
        assert claimed_activation.verification_session_id is not None
        session_id = claimed_activation.verification_session_id
        verification_event_id = verification_event.id
        outbox_payload = dict(verification_event.payload)

        def delivery_now() -> datetime:
            return now + timedelta(minutes=1)

        async def verification_handler(event: OutboxEvent) -> None:
            await deliver_livekit_verification_dispatch(
                event,
                session_factory=session_factory,
                provider=provider,
                token_config=TEST_DISPATCH_TOKEN_CONFIG,
                livekit_agent_name="ai-call-agent",
                now=delivery_now,
            )

        delivery_result = await deliver_outbox_batch(
            session_factory=session_factory,
            handlers={"livekit.verification_dispatch": verification_handler},
            observability=object(),
            now=delivery_now,
        )

        assert len(provider.create_calls) == 1
        dispatch_metadata = json.loads(provider.create_calls[0]["metadata"])
        completion_token = dispatch_metadata["completion_token"]
        normal_call_token = create_dispatch_token(
            call_id=str(uuid4()),
            user_id=str(user_id),
            agent_config_id=str(uuid4()),
            config=TEST_DISPATCH_TOKEN_CONFIG,
        )

        from app.routers.activation import router as activation_router

        async def override_session():
            async with session_factory() as session:
                yield session

        completion_app = FastAPI()
        completion_app.include_router(activation_router)
        install_test_api_runtime(
            completion_app,
            settings=Settings(
                app_env="test",
                database_url="sqlite+aiosqlite://",
                redis_url="redis://explicit-verification.invalid/0",
                agent_dispatch_jwt_secret=(TEST_DISPATCH_TOKEN_CONFIG.secret),
                agent_dispatch_jwt_ttl_seconds=(TEST_DISPATCH_TOKEN_CONFIG.ttl_seconds),
            ),
        )
        completion_app.dependency_overrides[get_session] = override_session
        transport = httpx.ASGITransport(app=completion_app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            rejected = await client.post(
                f"/api/activation/verification/{session_id}/complete",
                headers={"x-verification-token": normal_call_token},
                json={},
            )
            completed = await client.post(
                f"/api/activation/verification/{session_id}/complete",
                headers={"x-verification-token": completion_token},
                json={"schema_version": 1},
            )

        replay_result = await LiveKitDispatchService(
            db_session,
            activation_flow_enabled=True,
            realtime_service=realtime,
            recording_service=recording,
            now_provider=lambda: now + timedelta(minutes=2),
        ).handle_participant_joined(
            _sip_join(
                event_id="privacy-normal-replay",
                room_name="privacy-normal-replay-room",
            )
        )

    db_session.expire_all()
    after = await _privacy_counts(db_session)
    stored_activation = await db_session.get(CustomerActivation, activation_id)
    stored_phone = await db_session.get(PhoneNumber, phone_id)
    stored_event = await db_session.get(OutboxEvent, verification_event_id)
    activation_event_types = list(
        (
            await db_session.scalars(
                select(ActivationEvent.event_type).order_by(
                    ActivationEvent.created_at,
                    ActivationEvent.id,
                )
            )
        ).all()
    )

    assert claim_result.status == "verification_claimed"
    assert delivery_result == {
        "claimed": 1,
        "delivered": 1,
        "retried": 0,
        "failed": 0,
    }
    assert rejected.status_code == 401
    assert rejected.json() == {"detail": "Invalid verification token"}
    assert completed.status_code == 200
    assert completed.json() == {
        "schema_version": 1,
        "status": "verified",
        "session_id": session_id,
    }
    assert replay_result.status == "denied"
    assert stored_activation is not None
    assert stored_phone is not None
    assert stored_phone.id == phone_id
    assert stored_phone.user_id == user_id
    assert stored_activation.verification_status == "succeeded"
    assert stored_event is not None
    assert stored_event.status == "delivered"
    assert len(activation_event_types) == 3
    assert set(activation_event_types) == {
        "verification_window_opened",
        "verification_window_claimed",
        "verification_window_succeeded",
    }

    assert after == _PrivacyCounts(
        calls=before.calls,
        messages=before.messages,
        usage_ledgers=before.usage_ledgers,
        notifications=before.notifications,
        summary_events=before.summary_events,
        recording_operations=before.recording_operations,
        recording_events=before.recording_events,
        activation_events=before.activation_events + 3,
    )
    assert realtime.events == []
    assert recording.starts == []

    assert set(dispatch_metadata) == {
        "schema_version",
        "job_type",
        "verification_session_id",
        "user_id",
        "agent_identity",
        "completion_token",
        "message",
        "tts_provider",
    }
    assert dispatch_metadata["job_type"] == "forwarding_verification"
    assert dispatch_metadata["verification_session_id"] == session_id
    assert dispatch_metadata["user_id"] == str(user_id)
    assert completion_token not in json.dumps(outbox_payload)
    for sentinel in (
        SOURCE_NUMBER,
        PRESVO_NUMBER,
        OWNER_SENTINEL,
        BUSINESS_SENTINEL,
        DESCRIPTION_SENTINEL,
        RECEPTIONIST_SENTINEL,
    ):
        assert sentinel not in json.dumps(dispatch_metadata)

    rendered_logs = "\n".join(
        f"{record.getMessage()} {record.__dict__!r}" for record in caplog.records
    )
    for secret in (
        completion_token,
        normal_call_token,
        SOURCE_NUMBER,
        PRESVO_NUMBER,
        OWNER_SENTINEL,
        BUSINESS_SENTINEL,
        DESCRIPTION_SENTINEL,
        RECEPTIONIST_SENTINEL,
    ):
        assert secret not in rendered_logs
