import json
import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.provider_failures import ProviderFailure
from app.models.agent_config import AgentConfig
from app.models.call import Call
from app.models.outbox_event import OutboxEvent
from app.models.phone_number import PhoneNumber
from app.models.phone_number_provisioning import PhoneNumberProvisioning
from app.models.recording_egress_operation import RecordingEgressOperation
from app.models.subscription import Subscription
from app.models.usage_ledger import UsageLedger
from app.models.user import User
from app.providers.livekit_dispatch.base import LiveKitDispatch
from presvo_contracts import (
    AGENT_NAME_MAX_LENGTH,
    CustomerCallDispatch,
    ForwardingVerificationDispatch,
    create_contract,
    dump_contract,
    dump_contract_json,
    parse_dispatch,
)
from app.services.outbox_service import OutboxService
from app.services.recording_lifecycle_service import RecordingLifecycleService
from app.workers.outbox.delivery import outbox_delivery_job
from app.workers.outbox.failures import OutboxDeliveryError
from app.workers.outbox import customer_dispatch
from app.workers.outbox.customer_dispatch import deliver_livekit_dispatch


@pytest.fixture(autouse=True)
def _activation_flow_defaults_off_for_legacy_outbox_tests(
    monkeypatch: pytest.MonkeyPatch,
):
    from app.core.config import get_settings

    monkeypatch.setenv("ACTIVATION_FLOW_ENABLED", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class _Provider:
    def __init__(
        self, *, timeout_after_create: bool = False, always_fail: bool = False
    ):
        self.dispatches: list[LiveKitDispatch] = []
        self.list_calls: list[str] = []
        self.create_calls: list[dict] = []
        self.timeout_after_create = timeout_after_create
        self.always_fail = always_fail

    async def list_dispatches(self, *, room_name: str) -> list[LiveKitDispatch]:
        self.list_calls.append(room_name)
        return list(self.dispatches)

    async def create_dispatch(self, *, agent_name: str, room_name: str, metadata: str):
        self.create_calls.append(
            {"agent_name": agent_name, "room_name": room_name, "metadata": metadata}
        )
        if self.always_fail:
            raise ProviderFailure(
                provider="livekit",
                operation="create_dispatch",
                disposition="retryable",
                error_class="timeout",
            )
        created = LiveKitDispatch(
            id="dispatch-1",
            agent_name=agent_name,
            room=room_name,
            metadata=metadata,
            state="active",
        )
        self.dispatches.append(created)
        if self.timeout_after_create:
            raise ProviderFailure(
                provider="livekit",
                operation="create_dispatch",
                disposition="retryable",
                error_class="timeout",
            )
        return created


class _ForeignCreateProvider(_Provider):
    async def create_dispatch(self, *, agent_name: str, room_name: str, metadata: str):
        self.create_calls.append(
            {"agent_name": agent_name, "room_name": room_name, "metadata": metadata}
        )
        return LiveKitDispatch(
            id="dispatch-foreign",
            agent_name="Other",
            room="other-room",
            metadata=json.dumps({"call_id": "00000000-0000-0000-0000-000000000001"}),
            state="active",
        )


def _reconciliation_snapshot(*, persisted_dispatch_id: str | None = None):
    call_id = UUID("00000000-0000-0000-0000-000000000011")
    return customer_dispatch._DispatchSnapshot(
        call_id=call_id,
        user_id=UUID("00000000-0000-0000-0000-000000000012"),
        agent_config_id=UUID("00000000-0000-0000-0000-000000000013"),
        room_name="reconciliation-room",
        worker_name="reconciliation-worker",
        metadata="",
        persisted_dispatch_id=persisted_dispatch_id,
    )


def _customer_reconciliation_metadata(snapshot) -> dict[str, object]:
    return dump_contract(
        create_contract(
            CustomerCallDispatch,
            job_type="customer_call",
            call_id=snapshot.call_id,
            user_id=snapshot.user_id,
            agent_config_id=snapshot.agent_config_id,
            agent_identity=f"agent-call-{snapshot.call_id}",
            agent_name="Ava",
            owner_name="Owner",
            owner_context=None,
            system_prompt="Be helpful.",
            knowledge_base="Hours",
            pipeline_mode="stt_llm_tts",
            minutes_remaining=1,
            allowed_duration_seconds=60,
            dispatch_token="private-token",
        )
    )


def _customer_reconciliation_dispatch(
    snapshot,
    metadata: str,
    *,
    dispatch_id: str = "reconciled-dispatch",
) -> LiveKitDispatch:
    return LiveKitDispatch(
        id=dispatch_id,
        agent_name=snapshot.worker_name,
        room=snapshot.room_name,
        metadata=metadata,
        state="active",
    )


def test_customer_reconciliation_requires_a_valid_matching_customer_contract() -> None:
    snapshot = _reconciliation_snapshot()
    valid = _customer_reconciliation_metadata(snapshot)
    wrong_variant = dump_contract_json(
        create_contract(
            ForwardingVerificationDispatch,
            job_type="forwarding_verification",
            verification_session_id=UUID("00000000-0000-0000-0000-000000000014"),
            user_id=snapshot.user_id,
            agent_identity="agent-verification-test",
            completion_token="private-token",
            message="Forwarding test successful. Return to Presvo to go live.",
            tts_provider="speechmatics",
        )
    )
    invalid_metadata = {
        "malformed": "not-json",
        "unsupported_version": json.dumps(valid | {"schema_version": 2}),
        "wrong_variant": wrong_variant,
        "bad_uuid": json.dumps(valid | {"call_id": "not-a-uuid"}),
        "mismatched_uuid": json.dumps(
            valid | {"call_id": "00000000-0000-0000-0000-000000000015"}
        ),
        "mismatched_user_id": json.dumps(
            valid | {"user_id": "00000000-0000-0000-0000-000000000016"}
        ),
        "mismatched_agent_config_id": json.dumps(
            valid | {"agent_config_id": "00000000-0000-0000-0000-000000000017"}
        ),
        "mismatched_agent_identity": json.dumps(
            valid | {"agent_identity": "FOREIGN_AGENT_IDENTITY_SENTINEL"}
        ),
    }

    reconciled = customer_dispatch._reconcile_dispatches(
        snapshot,
        [_customer_reconciliation_dispatch(snapshot, json.dumps(valid))],
    )

    assert reconciled is not None
    assert reconciled.id == "reconciled-dispatch"
    for metadata in invalid_metadata.values():
        with pytest.raises(OutboxDeliveryError) as caught:
            customer_dispatch._reconcile_dispatches(
                snapshot,
                [_customer_reconciliation_dispatch(snapshot, metadata)],
            )
        assert caught.value.error_code == "dispatch_conflict"
        assert caught.value.retryable is False


def test_customer_reconciliation_rejects_mismatched_persisted_identity() -> None:
    snapshot = _reconciliation_snapshot(persisted_dispatch_id="persisted-other")
    with pytest.raises(OutboxDeliveryError) as caught:
        customer_dispatch._reconcile_dispatches(
            snapshot,
            [
                _customer_reconciliation_dispatch(
                    snapshot,
                    json.dumps(_customer_reconciliation_metadata(snapshot)),
                )
            ],
        )

    assert caught.value.error_code == "dispatch_conflict"
    assert caught.value.retryable is False


@pytest.mark.anyio
async def test_customer_reconciliation_never_persists_foreign_agent_identity(
    db_session,
    monkeypatch,
    caplog,
) -> None:
    call, event, _subscription = await _seed_dispatch(db_session)
    call_id = call.id
    snapshot = customer_dispatch._DispatchSnapshot(
        call_id=call.id,
        user_id=call.user_id,
        agent_config_id=call.agent_config_id,
        room_name=call.livekit_room_id,
        worker_name="reconciliation-worker",
        metadata="",
        persisted_dispatch_id=None,
    )
    metadata = _customer_reconciliation_metadata(snapshot) | {
        "agent_identity": "FOREIGN_AGENT_IDENTITY_SENTINEL"
    }
    provider = _Provider()
    provider.dispatches.append(
        _customer_reconciliation_dispatch(
            snapshot,
            json.dumps(metadata),
            dispatch_id="foreign-identity-dispatch",
        )
    )
    monkeypatch.setenv("LIVEKIT_AGENT_NAME", snapshot.worker_name)
    from app.core.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setattr(
        "app.workers.outbox.customer_dispatch.create_dispatch_token",
        lambda **_kwargs: "dispatch-jwt",
    )
    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    with caplog.at_level("INFO"), pytest.raises(OutboxDeliveryError) as caught:
        await deliver_livekit_dispatch(
            {"session_factory": session_factory, "livekit_dispatch_provider": provider},
            event,
        )

    db_session.expire_all()
    stored = await db_session.get(Call, call_id)
    assert caught.value.error_code == "dispatch_conflict"
    assert caught.value.retryable is False
    assert stored is not None
    assert stored.livekit_dispatch_id is None
    assert provider.create_calls == []
    assert "FOREIGN_AGENT_IDENTITY_SENTINEL" not in caplog.text


@pytest.mark.anyio
async def test_customer_empty_provider_dispatch_id_is_conflict_without_creation_or_persistence(
    db_session,
    monkeypatch,
) -> None:
    call, event, _subscription = await _seed_dispatch(db_session)
    call_id = call.id
    snapshot = customer_dispatch._DispatchSnapshot(
        call_id=call.id,
        user_id=call.user_id,
        agent_config_id=call.agent_config_id,
        room_name=call.livekit_room_id,
        worker_name="reconciliation-worker",
        metadata="",
        persisted_dispatch_id=None,
    )
    empty_id_dispatch = _customer_reconciliation_dispatch(
        snapshot,
        json.dumps(_customer_reconciliation_metadata(snapshot)),
        dispatch_id="",
    )
    with pytest.raises(OutboxDeliveryError) as reconciliation_error:
        customer_dispatch._reconcile_dispatches(snapshot, [empty_id_dispatch])

    provider = _Provider()
    provider.dispatches.append(empty_id_dispatch)
    monkeypatch.setenv("LIVEKIT_AGENT_NAME", snapshot.worker_name)
    from app.core.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setattr(
        "app.workers.outbox.customer_dispatch.create_dispatch_token",
        lambda **_kwargs: "dispatch-jwt",
    )
    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    with pytest.raises(OutboxDeliveryError) as caught:
        await deliver_livekit_dispatch(
            {"session_factory": session_factory, "livekit_dispatch_provider": provider},
            event,
        )

    db_session.expire_all()
    stored = await db_session.get(Call, call_id)
    assert reconciliation_error.value.error_code == "dispatch_conflict"
    assert reconciliation_error.value.retryable is False
    assert caught.value.error_code == "dispatch_conflict"
    assert caught.value.retryable is False
    assert stored is not None
    assert stored.livekit_dispatch_id is None
    assert provider.create_calls == []


async def _seed_dispatch(
    db_session,
    *,
    owner_name: str | None = None,
    business_display_name: str | None = None,
    user_id: UUID | None = None,
    config_id: UUID | None = None,
    call_id: UUID | None = None,
    with_provisioning: bool = False,
):
    from app.models.user import User

    now = datetime.now(UTC)
    user = User(
        id=user_id,
        clerk_user_id="outbox-user",
        email="outbox@example.com",
        full_name=owner_name,
    )
    db_session.add(user)
    await db_session.flush()
    phone = PhoneNumber(
        user_id=user.id,
        e164="+33999888777",
        country_code="FR",
        provider="telnyx",
        provider_number_id="number-outbox",
        provider_connection_name="app-active",
        is_active=True,
    )
    config = AgentConfig(
        id=config_id,
        user_id=user.id,
        agent_name="Ava",
        business_display_name=business_display_name,
        owner_context="Sam at Bakery",
        system_prompt="Be concise",
        knowledge_base="Hours 9-5",
        pipeline_mode="stt_llm_tts",
        is_enabled=True,
    )
    subscription = Subscription(
        user_id=user.id,
        stripe_customer_id="cus-outbox",
        stripe_subscription_id="sub-outbox",
        plan_tier="starter",
        status="active",
        allocated_minutes=60,
        current_period_start=now - timedelta(days=1),
        current_period_end=now + timedelta(days=1),
    )
    db_session.add_all([phone, config, subscription])
    await db_session.flush()
    if with_provisioning:
        db_session.add(
            PhoneNumberProvisioning(
                user_id=user.id,
                phone_number_id=phone.id,
                target_country_code="FR",
                status="succeeded",
                attempt_count=1,
                can_retry=False,
            )
        )
    call = Call(
        id=call_id,
        user_id=user.id,
        phone_number_id=phone.id,
        agent_config_id=config.id,
        livekit_room_id="room-outbox",
        caller_number="+33123456789",
        status="pending",
    )
    db_session.add_all(
        [
            call,
            UsageLedger(
                user_id=user.id,
                event_type="invoice_paid_reset",
                source_id="invoice-outbox",
                minutes_delta=60,
                balance_after=60,
            ),
        ]
    )
    await db_session.flush()
    event = await OutboxService(db_session).add(
        topic="livekit.dispatch",
        aggregate_type="call",
        aggregate_id=call.id,
        idempotency_key=f"livekit.dispatch:{call.id}",
        payload={
            "call_id": str(call.id),
            "lifecycle_generation": user.lifecycle_generation,
        },
    )
    await db_session.commit()
    return call, event, subscription


async def _add_migrated_pending_recording(
    db_session,
    call: Call,
    *,
    now: datetime,
    suffix: str,
) -> RecordingEgressOperation:
    provider_egress_id = f"egress-migrated-{suffix}"
    object_key = f"calls/{call.user_id}/{call.id}.ogg"
    call.recording_object_key = object_key
    call.recording_egress_id = provider_egress_id
    call.recording_url = f"https://recordings.example/{suffix}.ogg"
    operation = RecordingEgressOperation(
        id=call.id,
        call_id=call.id,
        room_name=call.livekit_room_id,
        legacy_incomplete=False,
        expected_object_key=object_key,
        provider_egress_id=provider_egress_id,
        start_state="started",
    )
    db_session.add(operation)
    await db_session.flush()
    await OutboxService(db_session, now_provider=lambda: now).add(
        topic="recording.reconcile",
        aggregate_type="recording-egress-operation",
        aggregate_id=operation.id,
        idempotency_key=f"recording.reconcile:{operation.id}:start",
        payload={"operation_id": str(operation.id)},
        next_attempt_at=now + timedelta(days=30),
    )
    await db_session.flush()
    return operation


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("owner_name", "expected_owner_name"),
    [
        ("Sam Rivera", "Sam Rivera"),
        (None, "the business"),
        ("  ", "the business"),
    ],
)
async def test_dispatch_handler_creates_and_persists_provider_identity(
    db_session,
    monkeypatch,
    owner_name: str | None,
    expected_owner_name: str,
) -> None:
    call, event, _subscription = await _seed_dispatch(
        db_session,
        owner_name=owner_name,
    )
    assert await db_session.scalar(select(PhoneNumberProvisioning)) is None
    provider = _Provider()
    monkeypatch.setenv("LIVEKIT_AGENT_NAME", "configured-worker")
    monkeypatch.setenv("MAX_CALL_DURATION_SECONDS", "900")
    from app.core.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setattr(
        "app.workers.outbox.customer_dispatch.create_dispatch_token",
        lambda **_kwargs: "dispatch-jwt",
    )
    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    await deliver_livekit_dispatch(
        {"session_factory": session_factory, "livekit_dispatch_provider": provider},
        event,
    )

    await db_session.refresh(call)
    assert call.livekit_dispatch_id == "dispatch-1"
    assert provider.list_calls == ["room-outbox"]
    assert len(provider.create_calls) == 1
    assert provider.create_calls[0]["agent_name"] == "configured-worker"
    metadata = json.loads(provider.create_calls[0]["metadata"])
    assert metadata["agent_name"] == "Ava"
    assert metadata["schema_version"] == 1
    assert metadata["job_type"] == "customer_call"
    assert metadata["call_id"] == str(call.id)
    assert metadata["agent_identity"] == f"agent-call-{call.id}"
    assert metadata["owner_name"] == expected_owner_name
    assert metadata["dispatch_token"] == "dispatch-jwt"
    assert metadata["minutes_remaining"] == 60
    assert metadata["allowed_duration_seconds"] == 900
    assert set(metadata) == {
        "schema_version",
        "job_type",
        "call_id",
        "user_id",
        "agent_config_id",
        "agent_identity",
        "agent_name",
        "owner_name",
        "owner_context",
        "system_prompt",
        "knowledge_base",
        "pipeline_mode",
        "minutes_remaining",
        "allowed_duration_seconds",
        "dispatch_token",
    }
    assert (
        dump_contract(parse_dispatch(provider.create_calls[0]["metadata"])) == metadata
    )
    assert "+33999888777" not in provider.create_calls[0]["metadata"]
    assert "+33123456789" not in provider.create_calls[0]["metadata"]


@pytest.mark.anyio
async def test_dispatch_greeting_uses_projected_business_name_and_bounds_owner_context(
    db_session,
    monkeypatch,
) -> None:
    call, event, _subscription = await _seed_dispatch(
        db_session,
        owner_name="Morgan Rivera",
        business_display_name="Atelier Nord",
    )
    call_id = call.id
    config = await db_session.scalar(select(AgentConfig))
    assert config is not None
    config.owner_context = "Owner name: Morgan Rivera\nBusiness name: Atelier Nord"
    await db_session.commit()
    provider = _Provider()
    monkeypatch.setattr(
        "app.workers.outbox.customer_dispatch.create_dispatch_token",
        lambda **_kwargs: "dispatch-jwt",
    )
    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    await deliver_livekit_dispatch(
        {"session_factory": session_factory, "livekit_dispatch_provider": provider},
        event,
    )

    metadata = json.loads(provider.create_calls[0]["metadata"])
    assert metadata["owner_name"] == "Atelier Nord"
    assert metadata["owner_context"] == (
        "Owner name: Morgan Rivera\nBusiness name: Atelier Nord"
    )
    assert "Morgan Rivera" not in metadata["owner_name"]
    db_session.expire_all()
    stored_call = await db_session.get(Call, call_id)
    assert stored_call is not None
    assert stored_call.livekit_dispatch_id == "dispatch-1"


@pytest.mark.anyio
async def test_activation_flow_missing_business_name_fails_dispatch_closed(
    db_session,
    monkeypatch,
) -> None:
    call, event, _subscription = await _seed_dispatch(
        db_session,
        owner_name="Legacy Owner",
        business_display_name=None,
        with_provisioning=True,
    )
    from app.models.business_profile import BusinessProfile
    from app.models.customer_activation import CustomerActivation
    from app.services.routing_fingerprint import routing_fingerprint

    phone = await db_session.get(PhoneNumber, call.phone_number_id)
    config = await db_session.get(AgentConfig, call.agent_config_id)
    assert phone is not None
    assert config is not None
    now = datetime.now(UTC)
    profile = BusinessProfile(
        user_id=call.user_id,
        owner_name="Legacy Owner",
        business_name="Atelier Nord",
        business_type="Plomberie",
        public_description="Dépannage et installation.",
        timezone="Europe/Paris",
        business_hours={"monday": {"closed": True, "intervals": []}},
        existing_phone_e164="+33199000300",
        confirmed_carrier="orange",
        receptionist_name="Ava",
        content_revision=2,
        routing_revision=2,
    )
    activation = CustomerActivation(
        user_id=call.user_id,
        profile_confirmed_revision=profile.content_revision,
        profile_confirmed_at=now - timedelta(hours=2),
        provisioning_consented_at=now - timedelta(hours=1),
        verification_status="succeeded",
        forwarding_verified_at=now - timedelta(minutes=10),
        go_live_requested_at=now - timedelta(minutes=5),
        go_live_approved_at=now - timedelta(minutes=5),
        activated_at=now - timedelta(minutes=4),
    )
    config.profile_projection_revision = profile.content_revision
    db_session.add_all([profile, activation])
    await db_session.flush()
    activation.verified_routing_fingerprint = routing_fingerprint(profile, phone)
    await db_session.commit()
    provider = _Provider()
    monkeypatch.setenv("ACTIVATION_FLOW_ENABLED", "true")
    from app.core.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setattr(
        "app.workers.outbox.customer_dispatch.create_dispatch_token",
        lambda **_kwargs: "dispatch-jwt",
    )
    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    try:
        with pytest.raises(OutboxDeliveryError) as exc_info:
            await deliver_livekit_dispatch(
                {
                    "session_factory": session_factory,
                    "livekit_dispatch_provider": provider,
                },
                event,
            )
    finally:
        get_settings.cache_clear()

    assert exc_info.value.error_code == "dispatch_configuration"
    assert exc_info.value.retryable is False
    assert provider.list_calls == []
    assert provider.create_calls == []


@pytest.mark.anyio
async def test_default_guided_projection_serializes_through_dispatch_contract(
    db_session,
    monkeypatch,
) -> None:
    call, event, _subscription = await _seed_dispatch(
        db_session,
        owner_name="Morgan Rivera",
        with_provisioning=True,
    )
    from app.models.business_profile import BusinessProfile
    from app.models.customer_activation import CustomerActivation
    from app.services.receptionist_projection_service import (
        ReceptionistProjectionService,
    )
    from app.services.routing_fingerprint import routing_fingerprint

    phone = await db_session.get(PhoneNumber, call.phone_number_id)
    config = await db_session.get(AgentConfig, call.agent_config_id)
    assert phone is not None
    assert config is not None
    now = datetime.now(UTC)
    profile = BusinessProfile(
        user_id=call.user_id,
        owner_name="Morgan Rivera",
        business_name="Atelier Nord",
        business_type="Bicycle repair",
        public_description="Repairs city bicycles.",
        timezone="Europe/Paris",
        business_hours={"monday": {"closed": True, "intervals": []}},
        existing_phone_e164="+33199000300",
        confirmed_carrier="orange",
        receptionist_name="Claire",
        content_revision=2,
        routing_revision=2,
    )
    ReceptionistProjectionService().project(profile, config)
    assert config.system_prompt == ""
    assert config.knowledge_base
    activation = CustomerActivation(
        user_id=call.user_id,
        profile_confirmed_revision=profile.content_revision,
        profile_confirmed_at=now - timedelta(hours=2),
        provisioning_consented_at=now - timedelta(hours=1),
        verification_status="succeeded",
        forwarding_verified_at=now - timedelta(minutes=10),
        go_live_requested_at=now - timedelta(minutes=5),
        go_live_approved_at=now - timedelta(minutes=5),
        activated_at=now - timedelta(minutes=4),
    )
    db_session.add_all([profile, activation])
    await db_session.flush()
    activation.verified_routing_fingerprint = routing_fingerprint(profile, phone)
    await db_session.commit()
    provider = _Provider()
    monkeypatch.setenv("ACTIVATION_FLOW_ENABLED", "true")
    from app.core.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setattr(
        "app.workers.outbox.customer_dispatch.create_dispatch_token",
        lambda **_kwargs: "dispatch-jwt",
    )
    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    try:
        await deliver_livekit_dispatch(
            {
                "session_factory": session_factory,
                "livekit_dispatch_provider": provider,
            },
            event,
        )
    finally:
        get_settings.cache_clear()

    metadata = parse_dispatch(provider.create_calls[0]["metadata"])
    assert metadata.system_prompt == ""
    assert metadata.knowledge_base == config.knowledge_base


@pytest.mark.anyio
async def test_unnamed_automatic_dispatch_does_not_block_named_dispatch(
    db_session,
    monkeypatch,
) -> None:
    call, event, _subscription = await _seed_dispatch(db_session)
    provider = _Provider()
    provider.dispatches.append(
        LiveKitDispatch(
            id="dispatch-automatic",
            agent_name="",
            room="room-outbox",
            metadata="",
            state="active",
        )
    )
    monkeypatch.setenv("LIVEKIT_AGENT_NAME", "configured-worker")
    from app.core.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setattr(
        "app.workers.outbox.customer_dispatch.create_dispatch_token",
        lambda **_kwargs: "dispatch-jwt",
    )
    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    await deliver_livekit_dispatch(
        {"session_factory": session_factory, "livekit_dispatch_provider": provider},
        event,
    )

    await db_session.refresh(call)
    assert call.livekit_dispatch_id == "dispatch-1"
    assert len(provider.create_calls) == 1


@pytest.mark.anyio
async def test_create_then_timeout_reconciles_to_one_effective_dispatch(
    db_session,
    monkeypatch,
) -> None:
    call, event, _subscription = await _seed_dispatch(db_session)
    provider = _Provider(timeout_after_create=True)
    monkeypatch.setattr(
        "app.workers.outbox.customer_dispatch.create_dispatch_token",
        lambda **_kwargs: "dispatch-jwt",
    )
    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    await deliver_livekit_dispatch(
        {"session_factory": session_factory, "livekit_dispatch_provider": provider},
        event,
    )

    await db_session.refresh(call)
    assert call.livekit_dispatch_id == "dispatch-1"
    assert len(provider.dispatches) == 1
    assert len(provider.create_calls) == 1
    assert provider.list_calls == ["room-outbox", "room-outbox"]


@pytest.mark.anyio
async def test_terminal_create_failure_bypasses_dispatch_reconciliation(
    db_session,
    monkeypatch,
) -> None:
    class TerminalCreateProvider(_Provider):
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
            raise ProviderFailure(
                provider="livekit",
                operation="create_dispatch",
                disposition="terminal",
                error_class="authentication",
            )

    _call, event, _subscription = await _seed_dispatch(db_session)
    provider = TerminalCreateProvider()
    monkeypatch.setattr(
        "app.workers.outbox.customer_dispatch.create_dispatch_token",
        lambda **_kwargs: "dispatch-jwt",
    )
    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    with pytest.raises(OutboxDeliveryError) as exc_info:
        await deliver_livekit_dispatch(
            {"session_factory": session_factory, "livekit_dispatch_provider": provider},
            event,
        )

    assert exc_info.value.error_code == "provider_terminal"
    assert provider.list_calls == ["room-outbox"]
    assert len(provider.create_calls) == 1


@pytest.mark.anyio
async def test_stale_account_generation_never_dispatches_customer_call(
    db_session,
    monkeypatch,
) -> None:
    call, event, _subscription = await _seed_dispatch(db_session)
    user = await db_session.get(User, call.user_id)
    assert user is not None
    user.lifecycle_generation = 2
    event.payload = {
        "call_id": str(call.id),
        "lifecycle_generation": 1,
    }
    await db_session.commit()
    provider = _Provider()
    monkeypatch.setattr(
        "app.workers.outbox.customer_dispatch.create_dispatch_token",
        lambda **_kwargs: "dispatch-jwt",
    )
    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    with pytest.raises(OutboxDeliveryError) as exc_info:
        await deliver_livekit_dispatch(
            {"session_factory": session_factory, "livekit_dispatch_provider": provider},
            event,
        )

    assert exc_info.value.error_code == "dispatch_ineligible"
    assert exc_info.value.retryable is False
    assert provider.list_calls == []
    assert provider.create_calls == []


@pytest.mark.anyio
async def test_deactivation_during_customer_dispatch_reconciliation_prevents_create(
    db_session,
    monkeypatch,
) -> None:
    call, event, _subscription = await _seed_dispatch(db_session)
    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    class _DeactivatingListProvider(_Provider):
        async def list_dispatches(
            self,
            *,
            room_name: str,
        ) -> list[LiveKitDispatch]:
            async with session_factory() as session:
                user = await session.get(User, call.user_id)
                assert user is not None
                user.status = "deactivating"
                user.lifecycle_generation += 1
                await session.commit()
            return await super().list_dispatches(room_name=room_name)

    provider = _DeactivatingListProvider()
    monkeypatch.setattr(
        "app.workers.outbox.customer_dispatch.create_dispatch_token",
        lambda **_kwargs: "dispatch-jwt",
    )

    with pytest.raises(OutboxDeliveryError) as exc_info:
        await deliver_livekit_dispatch(
            {"session_factory": session_factory, "livekit_dispatch_provider": provider},
            event,
        )

    assert exc_info.value.error_code == "dispatch_ineligible"
    assert exc_info.value.retryable is False
    assert provider.list_calls == ["room-outbox"]
    assert provider.create_calls == []


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
async def test_stale_readiness_never_calls_provider(
    db_session,
    monkeypatch,
    ineligible_case: str,
) -> None:
    _call, event, subscription = await _seed_dispatch(db_session)
    config = await db_session.scalar(select(AgentConfig))
    phone = await db_session.scalar(select(PhoneNumber))
    usage = await db_session.scalar(select(UsageLedger))
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
    elif ineligible_case == "called_number_mismatch":
        phone.e164 = ""

    await db_session.commit()
    provider = _Provider()
    monkeypatch.setattr(
        "app.workers.outbox.customer_dispatch.create_dispatch_token",
        lambda **_kwargs: "dispatch-jwt",
    )
    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    with pytest.raises(OutboxDeliveryError) as exc_info:
        await deliver_livekit_dispatch(
            {"session_factory": session_factory, "livekit_dispatch_provider": provider},
            event,
        )

    assert exc_info.value.error_code == "dispatch_ineligible"
    assert exc_info.value.retryable is False
    assert provider.list_calls == []
    assert provider.create_calls == []


@pytest.mark.anyio
async def test_disagreeing_phone_projection_never_calls_dispatch_provider(
    db_session,
    monkeypatch,
) -> None:
    call, event, _subscription = await _seed_dispatch(db_session)
    phone = await db_session.get(PhoneNumber, call.phone_number_id)
    phone.provider_connection_name = "app-disabled"
    phone.is_active = True
    await db_session.commit()
    provider = _Provider()
    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    with pytest.raises(OutboxDeliveryError) as exc_info:
        await deliver_livekit_dispatch(
            {"session_factory": session_factory, "livekit_dispatch_provider": provider},
            event,
        )

    assert exc_info.value.error_code == "dispatch_ineligible"
    assert provider.list_calls == []
    assert provider.create_calls == []


@pytest.mark.anyio
async def test_foreign_dispatch_is_a_terminal_conflict(db_session, monkeypatch) -> None:
    _call, event, _subscription = await _seed_dispatch(db_session)
    provider = _Provider()
    provider.dispatches.append(
        LiveKitDispatch(
            id="foreign",
            agent_name="Other",
            room="room-outbox",
            metadata=json.dumps({"call_id": "00000000-0000-0000-0000-000000000001"}),
            state="active",
        )
    )
    monkeypatch.setattr(
        "app.workers.outbox.customer_dispatch.create_dispatch_token",
        lambda **_kwargs: "dispatch-jwt",
    )
    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    with pytest.raises(OutboxDeliveryError) as exc_info:
        await deliver_livekit_dispatch(
            {"session_factory": session_factory, "livekit_dispatch_provider": provider},
            event,
        )

    assert exc_info.value.error_code == "dispatch_conflict"
    assert exc_info.value.retryable is False
    assert provider.create_calls == []


@pytest.mark.anyio
async def test_successful_create_response_must_match_requested_dispatch(
    db_session,
    monkeypatch,
) -> None:
    call, event, _subscription = await _seed_dispatch(db_session)
    provider = _ForeignCreateProvider()
    monkeypatch.setattr(
        "app.workers.outbox.customer_dispatch.create_dispatch_token",
        lambda **_kwargs: "dispatch-jwt",
    )
    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    with pytest.raises(OutboxDeliveryError) as exc_info:
        await deliver_livekit_dispatch(
            {"session_factory": session_factory, "livekit_dispatch_provider": provider},
            event,
        )

    await db_session.refresh(call)
    assert exc_info.value.error_code == "dispatch_conflict"
    assert call.livekit_dispatch_id is None


@pytest.mark.anyio
async def test_malformed_foreign_aggregate_is_terminal(db_session) -> None:
    _call, event, _subscription = await _seed_dispatch(db_session)
    event.aggregate_type = "user"
    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    with pytest.raises(OutboxDeliveryError) as exc_info:
        await deliver_livekit_dispatch(
            {
                "session_factory": session_factory,
                "livekit_dispatch_provider": _Provider(),
            },
            event,
        )

    assert exc_info.value.error_code == "dispatch_configuration"
    assert exc_info.value.retryable is False


@pytest.mark.anyio
async def test_sixth_provider_failure_atomically_fails_call(
    db_session, monkeypatch
) -> None:
    call, event, _subscription = await _seed_dispatch(db_session)
    operation = await _add_migrated_pending_recording(
        db_session,
        call,
        now=event.next_attempt_at,
        suffix="dispatch-exhausted",
    )
    operation_id = operation.id
    await db_session.commit()
    provider = _Provider(always_fail=True)
    monkeypatch.setattr(
        "app.workers.outbox.customer_dispatch.create_dispatch_token",
        lambda **_kwargs: "dispatch-jwt",
    )
    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    current_time = event.next_attempt_at + timedelta(seconds=1)
    ctx = {
        "session_factory": session_factory,
        "livekit_dispatch_provider": provider,
        "outbox_now": lambda: current_time,
    }

    async def complete_recording_reconcile(_ctx, _event) -> None:
        return None

    ctx["outbox_handlers"] = {
        "livekit.dispatch": deliver_livekit_dispatch,
        "recording.reconcile": complete_recording_reconcile,
    }

    for _attempt in range(6):
        await outbox_delivery_job(ctx)
        async with session_factory() as session:
            stored = await session.get(type(event), event.id)
            assert stored is not None
            current_time = stored.next_attempt_at

    await db_session.refresh(call)
    assert call.status == "failed"
    assert call.failure_code == "dispatch_provider_exhausted"
    db_session.expire_all()
    stored_operation = await db_session.get(RecordingEgressOperation, operation_id)
    stop_events = (
        await db_session.scalars(
            select(OutboxEvent).where(
                OutboxEvent.idempotency_key
                == f"recording.reconcile:{operation_id}:stop"
            )
        )
    ).all()
    assert stored_operation is not None
    assert stored_operation.id == operation_id
    assert stored_operation.provider_egress_id == "egress-migrated-dispatch-exhausted"
    assert stored_operation.stop_requested_at is not None
    assert len(stop_events) == 1
    assert stop_events[0].payload == {"operation_id": str(operation_id)}


@pytest.mark.anyio
async def test_terminal_dispatch_failure_rolls_back_when_recording_stop_fails(
    db_session,
    monkeypatch,
) -> None:
    call, event, _subscription = await _seed_dispatch(db_session)
    operation = await _add_migrated_pending_recording(
        db_session,
        call,
        now=event.next_attempt_at,
        suffix="dispatch-rollback",
    )
    operation_id = operation.id
    call_id = call.id
    event_id = event.id
    event.attempt_count = 5
    await db_session.commit()
    provider = _Provider(always_fail=True)
    monkeypatch.setattr(
        "app.workers.outbox.customer_dispatch.create_dispatch_token",
        lambda **_kwargs: "dispatch-jwt",
    )

    async def fail_request_stop(self, _call):
        raise RuntimeError("forced recording stop failure")

    monkeypatch.setattr(
        RecordingLifecycleService,
        "request_stop",
        fail_request_stop,
    )
    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    with pytest.raises(RuntimeError, match="forced recording stop failure"):
        await outbox_delivery_job(
            {
                "session_factory": session_factory,
                "livekit_dispatch_provider": provider,
                "outbox_now": lambda: event.next_attempt_at + timedelta(seconds=1),
            }
        )

    db_session.expire_all()
    stored_call = await db_session.get(Call, call_id)
    stored_event = await db_session.get(OutboxEvent, event_id)
    stored_operation = await db_session.get(RecordingEgressOperation, operation_id)
    stop_event = await db_session.scalar(
        select(OutboxEvent).where(
            OutboxEvent.idempotency_key == f"recording.reconcile:{operation_id}:stop"
        )
    )
    assert stored_call is not None
    assert stored_call.status == "pending"
    assert stored_call.failure_code is None
    assert stored_event is not None
    assert stored_event.status == "processing"
    assert stored_event.attempt_count == 6
    assert stored_event.last_error_code is None
    assert stored_operation is not None
    assert stored_operation.stop_requested_at is None
    assert stop_event is None


@pytest.mark.anyio
async def test_terminal_invalid_payload_releases_authoritative_aggregate_call(
    db_session,
) -> None:
    from app.models.user import User

    call, event, _subscription = await _seed_dispatch(db_session)
    other_user = User(clerk_user_id="payload-target", email="target@example.com")
    db_session.add(other_user)
    await db_session.flush()
    payload_target = Call(user_id=other_user.id, status="pending")
    db_session.add(payload_target)
    await db_session.flush()
    event.payload = {"call_id": str(payload_target.id)}
    await db_session.commit()
    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    async def terminal(_ctx, _event) -> None:
        raise OutboxDeliveryError("dispatch_configuration", retryable=False)

    result = await outbox_delivery_job(
        {
            "session_factory": session_factory,
            "outbox_handlers": {"livekit.dispatch": terminal},
            "outbox_now": lambda: event.next_attempt_at + timedelta(seconds=1),
        }
    )

    await db_session.refresh(call)
    assert result["failed"] == 1
    assert call.status == "failed"
    assert call.failure_code == "dispatch_configuration"
    await db_session.refresh(payload_target)
    assert payload_target.status == "pending"
    assert payload_target.failure_code is None


@pytest.mark.anyio
async def test_unsafe_dispatch_token_settings_fail_as_dispatch_configuration(
    db_session,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from app.core.config import get_settings

    call, event, _subscription = await _seed_dispatch(db_session)
    call_id = call.id
    event_id = event.id
    provider = _Provider()
    metrics: list[tuple[str, str]] = []
    monkeypatch.setenv("AGENT_DISPATCH_JWT_SECRET", "too-short")
    get_settings.cache_clear()
    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    with caplog.at_level(logging.CRITICAL):
        result = await outbox_delivery_job(
            {
                "session_factory": session_factory,
                "livekit_dispatch_provider": provider,
                "outbox_handlers": {"livekit.dispatch": deliver_livekit_dispatch},
                "outbox_now": lambda: event.next_attempt_at + timedelta(seconds=1),
                "outbox_terminal_failure_metric": (
                    lambda topic, code: metrics.append((topic, code))
                ),
            }
        )

    db_session.expire_all()
    stored_call = await db_session.get(Call, call_id)
    stored_event = await db_session.get(OutboxEvent, event_id)
    assert result == {"claimed": 1, "delivered": 0, "retried": 0, "failed": 1}
    assert stored_call is not None
    assert stored_call.status == "failed"
    assert stored_call.failure_code == "dispatch_configuration"
    assert stored_event is not None
    assert stored_event.status == "failed"
    assert stored_event.last_error_code == "dispatch_configuration"
    assert metrics == [("livekit.dispatch", "dispatch_configuration")]
    assert provider.list_calls == []
    assert provider.create_calls == []
    assert "event=outbox_internal_defect" not in caplog.text


@pytest.mark.anyio
async def test_terminal_dispatch_failure_never_overturns_connected_call(
    db_session,
) -> None:
    call, event, _subscription = await _seed_dispatch(db_session)
    call.status = "connected"
    await db_session.commit()
    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    async def terminal(_ctx, _event) -> None:
        raise OutboxDeliveryError("dispatch_ineligible", retryable=False)

    result = await outbox_delivery_job(
        {
            "session_factory": session_factory,
            "outbox_handlers": {"livekit.dispatch": terminal},
            "outbox_now": lambda: event.next_attempt_at + timedelta(seconds=1),
        }
    )

    await db_session.refresh(call)
    assert result["failed"] == 1
    assert call.status == "connected"
    assert call.failure_code is None
