import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.agent_config import AgentConfig
from app.models.call import Call
from app.models.phone_number import PhoneNumber
from app.models.subscription import Subscription
from app.models.usage_ledger import UsageLedger
from app.providers.livekit_dispatch.base import LiveKitDispatch
from app.schemas.agent_content import AGENT_NAME_MAX_LENGTH
from app.services.outbox_service import OutboxService
from app.workers.jobs.outbox_delivery import OutboxDeliveryError, outbox_delivery_job
from app.workers.jobs.outbox_topics import deliver_livekit_dispatch


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
            raise TimeoutError("RAW_PROVIDER_TIMEOUT")
        created = LiveKitDispatch(
            id="dispatch-1",
            agent_name=agent_name,
            room=room_name,
            metadata=metadata,
            state="active",
        )
        self.dispatches.append(created)
        if self.timeout_after_create:
            raise TimeoutError("RAW_PROVIDER_TIMEOUT_AFTER_CREATE")
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


async def _seed_dispatch(
    db_session,
    *,
    owner_name: str | None = None,
    business_display_name: str | None = None,
):
    from app.models.user import User

    now = datetime.now(UTC)
    user = User(
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
    call = Call(
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
        payload={"call_id": str(call.id)},
    )
    await db_session.commit()
    return call, event, subscription


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
    provider = _Provider()
    monkeypatch.setenv("LIVEKIT_AGENT_NAME", "configured-worker")
    monkeypatch.setenv("MAX_CALL_DURATION_SECONDS", "900")
    from app.core.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setattr(
        "app.workers.jobs.outbox_topics.create_dispatch_token",
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
    assert metadata["call_id"] == str(call.id)
    assert metadata["agent_identity"] == f"agent-call-{call.id}"
    assert metadata["owner_name"] == expected_owner_name
    assert metadata["dispatch_token"] == "dispatch-jwt"
    assert metadata["minutes_remaining"] == 60
    assert metadata["allowed_duration_seconds"] == 900
    assert set(metadata) == {
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
    config = await db_session.scalar(select(AgentConfig))
    assert config is not None
    config.owner_context = "Owner name: Morgan Rivera\nBusiness name: Atelier Nord"
    await db_session.commit()
    provider = _Provider()
    monkeypatch.setattr(
        "app.workers.jobs.outbox_topics.create_dispatch_token",
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
    assert call.livekit_dispatch_id == "dispatch-1"


@pytest.mark.anyio
async def test_activation_flow_missing_business_name_fails_dispatch_closed(
    db_session,
    monkeypatch,
) -> None:
    _call, event, _subscription = await _seed_dispatch(
        db_session,
        owner_name="Legacy Owner",
        business_display_name=None,
    )
    provider = _Provider()
    monkeypatch.setenv("ACTIVATION_FLOW_ENABLED", "true")
    from app.core.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setattr(
        "app.workers.jobs.outbox_topics.create_dispatch_token",
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
        "app.workers.jobs.outbox_topics.create_dispatch_token",
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
        "app.workers.jobs.outbox_topics.create_dispatch_token",
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
        "app.workers.jobs.outbox_topics.create_dispatch_token",
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
        "app.workers.jobs.outbox_topics.create_dispatch_token",
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
        "app.workers.jobs.outbox_topics.create_dispatch_token",
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
    provider = _Provider(always_fail=True)
    monkeypatch.setattr(
        "app.workers.jobs.outbox_topics.create_dispatch_token",
        lambda **_kwargs: "dispatch-jwt",
    )
    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    current_time = event.next_attempt_at + timedelta(seconds=1)
    ctx = {
        "session_factory": session_factory,
        "livekit_dispatch_provider": provider,
        "outbox_now": lambda: current_time,
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
