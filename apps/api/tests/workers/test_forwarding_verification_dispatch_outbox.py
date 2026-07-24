import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.verification_token import verify_verification_token
from app.models.call import Call
from app.models.customer_activation import CustomerActivation
from app.models.user import User
from app.providers.livekit_dispatch.base import LiveKitDispatch
from app.schemas import livekit as livekit_schemas
from app.services.outbox_service import OutboxService, SUPPORTED_OUTBOX_TOPICS
from app.workers.jobs import outbox_topics
from app.workers.jobs.outbox_delivery import OutboxDeliveryError, _validated_event_call_id


FIXED_NOW = datetime(2026, 7, 18, 10, 0, tzinfo=UTC)
SUCCESS_MESSAGE = "Forwarding test successful. Return to Presvo to go live."


def _metadata_model():
    model = getattr(livekit_schemas, "VerificationDispatchMetadata", None)
    assert model is not None, "verification dispatch metadata is missing"
    return model


def _handler():
    handler = getattr(outbox_topics, "deliver_livekit_verification_dispatch", None)
    assert handler is not None, "verification dispatch handler is missing"
    return handler


class _Provider:
    def __init__(
        self,
        *,
        timeout_after_create: bool = False,
        always_fail: bool = False,
        fail_recovery_list_once: bool = False,
    ) -> None:
        self.dispatches: list[LiveKitDispatch] = []
        self.list_calls: list[str] = []
        self.create_calls: list[dict[str, str]] = []
        self.timeout_after_create = timeout_after_create
        self.always_fail = always_fail
        self.fail_recovery_list_once = fail_recovery_list_once

    async def list_dispatches(self, *, room_name: str) -> list[LiveKitDispatch]:
        self.list_calls.append(room_name)
        if (
            self.fail_recovery_list_once
            and len(self.list_calls) == 2
            and self.dispatches
        ):
            self.fail_recovery_list_once = False
            raise TimeoutError("provider recovery list timeout")
        return list(self.dispatches)

    async def create_dispatch(
        self,
        *,
        agent_name: str,
        room_name: str,
        metadata: str,
    ) -> LiveKitDispatch:
        self.create_calls.append(
            {"agent_name": agent_name, "room_name": room_name, "metadata": metadata}
        )
        if self.always_fail:
            raise TimeoutError("provider timeout")
        created = LiveKitDispatch(
            id="verification-dispatch-1",
            agent_name=agent_name,
            room=room_name,
            metadata=metadata,
            state="active",
        )
        self.dispatches.append(created)
        if self.timeout_after_create:
            raise TimeoutError("provider timeout after create")
        return created


class _CompletingProvider(_Provider):
    def __init__(self, session_factory, activation_id) -> None:
        super().__init__()
        self.session_factory = session_factory
        self.activation_id = activation_id

    async def create_dispatch(
        self,
        *,
        agent_name: str,
        room_name: str,
        metadata: str,
    ) -> LiveKitDispatch:
        created = await super().create_dispatch(
            agent_name=agent_name,
            room_name=room_name,
            metadata=metadata,
        )
        async with self.session_factory() as session:
            activation = await session.get(CustomerActivation, self.activation_id)
            assert activation is not None
            activation.verification_status = "succeeded"
            activation.forwarding_verified_at = FIXED_NOW
            await session.commit()
        return created


async def _seed_verification_dispatch(db_session):
    user = User(
        clerk_user_id="verification-dispatch-owner",
        email="verification-dispatch-owner@example.invalid",
    )
    db_session.add(user)
    await db_session.flush()
    session_id = str(uuid4())
    activation = CustomerActivation(
        user_id=user.id,
        verification_window_started_at=FIXED_NOW - timedelta(minutes=1),
        verification_window_expires_at=FIXED_NOW + timedelta(minutes=9),
        verification_session_id=session_id,
        verification_claimed_at=FIXED_NOW,
        verification_status="claimed",
        verification_routing_fingerprint="a" * 64,
    )
    db_session.add(activation)
    await db_session.flush()
    event = await OutboxService(db_session).add(
        topic="livekit.verification_dispatch",
        aggregate_type="forwarding-verification",
        aggregate_id=activation.id,
        idempotency_key=f"livekit.verification_dispatch:{session_id}",
        payload={
            "activation_id": str(activation.id),
            "session_id": session_id,
            "room_name": "verification-dispatch-room",
        },
    )
    await db_session.commit()
    return user, activation, event


def test_verification_dispatch_metadata_is_exact_and_forbids_extras() -> None:
    model = _metadata_model()
    payload = {
        "job_type": "forwarding_verification",
        "verification_session_id": str(uuid4()),
        "user_id": str(uuid4()),
        "agent_identity": f"agent-verification-{uuid4()}",
        "completion_token": "scoped-token",
        "message": SUCCESS_MESSAGE,
        "tts_provider": "speechmatics",
    }

    metadata = model.model_validate(payload)

    assert metadata.model_dump() == payload
    assert model.model_validate(
        payload | {"tts_provider": "elevenlabs"}
    ).tts_provider == "elevenlabs"
    with pytest.raises(ValidationError):
        model.model_validate(payload | {"system_prompt": "customer content"})
    with pytest.raises(ValidationError):
        model.model_validate(payload | {"job_type": "customer_call"})


@pytest.mark.anyio
async def test_handler_creates_exact_verification_job_and_persists_identity(
    db_session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, activation, event = await _seed_verification_dispatch(db_session)
    activation_id = activation.id
    session_id = activation.verification_session_id
    user_id = user.id
    outbox_payload = dict(event.payload)
    provider = _Provider()
    monkeypatch.setenv("LIVEKIT_AGENT_NAME", "configured-worker")
    from app.core.config import get_settings

    get_settings.cache_clear()
    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    await _handler()(
        {
            "session_factory": session_factory,
            "livekit_dispatch_provider": provider,
            "verification_now": lambda: FIXED_NOW,
        },
        event,
    )

    db_session.expire_all()
    stored = await db_session.get(CustomerActivation, activation_id)
    assert stored is not None
    assert stored.verification_dispatch_id == "verification-dispatch-1"
    assert len(provider.create_calls) == 1
    created = provider.create_calls[0]
    assert created["agent_name"] == "configured-worker"
    assert created["room_name"] == "verification-dispatch-room"
    metadata = json.loads(created["metadata"])
    assert metadata["job_type"] == "forwarding_verification"
    assert metadata["verification_session_id"] == session_id
    assert metadata["user_id"] == str(user_id)
    assert metadata["agent_identity"] == (
        f"agent-verification-{session_id}"
    )
    assert metadata["message"] == SUCCESS_MESSAGE
    assert metadata["tts_provider"] == "speechmatics"
    assert set(metadata) == {
        "job_type",
        "verification_session_id",
        "user_id",
        "agent_identity",
        "completion_token",
        "message",
        "tts_provider",
    }
    verify_verification_token(
        metadata["completion_token"],
        expected_session_id=session_id,
        expected_user_id=str(user_id),
    )
    assert metadata["completion_token"] not in json.dumps(outbox_payload)
    assert await db_session.scalar(select(func.count()).select_from(Call)) == 0


@pytest.mark.anyio
async def test_matching_provider_dispatch_reconciles_without_create(db_session) -> None:
    _user, activation, event = await _seed_verification_dispatch(db_session)
    activation_id = activation.id
    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    provider = _Provider()
    provider.dispatches.append(
        LiveKitDispatch(
            id="verification-existing",
            agent_name="ai-call-agent",
            room="verification-dispatch-room",
            metadata=json.dumps(
                {
                    "job_type": "forwarding_verification",
                    "verification_session_id": activation.verification_session_id,
                }
            ),
            state="active",
        )
    )

    await _handler()(
        {
            "session_factory": session_factory,
            "livekit_dispatch_provider": provider,
            "verification_now": lambda: FIXED_NOW,
        },
        event,
    )

    db_session.expire_all()
    stored = await db_session.get(CustomerActivation, activation_id)
    assert stored is not None
    assert stored.verification_dispatch_id == "verification-existing"
    assert provider.create_calls == []


@pytest.mark.anyio
async def test_create_timeout_reconciles_to_one_verification_dispatch(db_session) -> None:
    _user, activation, event = await _seed_verification_dispatch(db_session)
    activation_id = activation.id
    provider = _Provider(timeout_after_create=True)
    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    await _handler()(
        {
            "session_factory": session_factory,
            "livekit_dispatch_provider": provider,
            "verification_now": lambda: FIXED_NOW,
        },
        event,
    )

    db_session.expire_all()
    stored = await db_session.get(CustomerActivation, activation_id)
    assert stored is not None
    assert stored.verification_dispatch_id == "verification-dispatch-1"
    assert len(provider.dispatches) == 1
    assert len(provider.create_calls) == 1
    assert provider.list_calls == [
        "verification-dispatch-room",
        "verification-dispatch-room",
    ]


@pytest.mark.anyio
async def test_success_before_persist_reconciles_on_retry_without_second_create(
    db_session,
) -> None:
    _user, activation, event = await _seed_verification_dispatch(db_session)
    activation_id = activation.id
    provider = _Provider(
        timeout_after_create=True,
        fail_recovery_list_once=True,
    )
    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    ctx = {
        "session_factory": session_factory,
        "livekit_dispatch_provider": provider,
        "verification_now": lambda: FIXED_NOW,
    }

    with pytest.raises(OutboxDeliveryError) as exc_info:
        await _handler()(ctx, event)
    await _handler()(ctx, event)

    assert exc_info.value.error_code == "provider_retryable"
    assert exc_info.value.retryable is True
    db_session.expire_all()
    stored = await db_session.get(CustomerActivation, activation_id)
    assert stored is not None
    assert stored.verification_dispatch_id == "verification-dispatch-1"
    assert len(provider.dispatches) == 1
    assert len(provider.create_calls) == 1


@pytest.mark.anyio
async def test_completion_race_persists_dispatch_only_for_the_same_session(
    db_session,
) -> None:
    _user, activation, event = await _seed_verification_dispatch(db_session)
    activation_id = activation.id
    session_id = event.payload["session_id"]
    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    provider = _CompletingProvider(session_factory, activation_id)

    await _handler()(
        {
            "session_factory": session_factory,
            "livekit_dispatch_provider": provider,
            "verification_now": lambda: FIXED_NOW,
        },
        event,
    )

    db_session.expire_all()
    stored = await db_session.get(CustomerActivation, activation_id)
    assert stored is not None
    assert stored.verification_status == "succeeded"
    assert stored.verification_session_id == session_id
    assert stored.verification_dispatch_id == "verification-dispatch-1"


@pytest.mark.anyio
@pytest.mark.parametrize("conflict_case", ["foreign", "duplicate", "persisted"])
async def test_foreign_duplicate_and_persisted_dispatch_conflicts_are_terminal(
    db_session,
    conflict_case: str,
) -> None:
    _user, activation, event = await _seed_verification_dispatch(db_session)
    provider = _Provider()
    matching = LiveKitDispatch(
        id="matching-dispatch",
        agent_name="ai-call-agent",
        room="verification-dispatch-room",
        metadata=json.dumps(
            {
                "job_type": "forwarding_verification",
                "verification_session_id": activation.verification_session_id,
            }
        ),
        state="active",
    )
    if conflict_case == "foreign":
        provider.dispatches.append(
            LiveKitDispatch(
                id="foreign-dispatch",
                agent_name="other-worker",
                room="verification-dispatch-room",
                metadata=json.dumps({"job_type": "customer_call"}),
                state="active",
            )
        )
    elif conflict_case == "duplicate":
        provider.dispatches.extend([matching, matching])
    else:
        activation.verification_dispatch_id = "persisted-other"
        await db_session.commit()
        provider.dispatches.append(matching)
    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    with pytest.raises(OutboxDeliveryError) as exc_info:
        await _handler()(
            {
                "session_factory": session_factory,
                "livekit_dispatch_provider": provider,
                "verification_now": lambda: FIXED_NOW,
            },
            event,
        )

    assert exc_info.value.error_code == "dispatch_conflict"
    assert exc_info.value.retryable is False
    assert provider.create_calls == []


@pytest.mark.anyio
@pytest.mark.parametrize("invalid_case", ["topic", "aggregate", "activation", "session"])
async def test_handler_rejects_mismatched_event_identity_without_provider_io(
    db_session,
    invalid_case: str,
) -> None:
    _user, _activation, event = await _seed_verification_dispatch(db_session)
    if invalid_case == "topic":
        event.topic = "livekit.dispatch"
    elif invalid_case == "aggregate":
        event.aggregate_type = "call"
    elif invalid_case == "activation":
        event.payload = event.payload | {"activation_id": str(uuid4())}
    else:
        event.payload = event.payload | {"session_id": str(uuid4())}
    provider = _Provider()
    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    with pytest.raises(OutboxDeliveryError) as exc_info:
        await _handler()(
            {
                "session_factory": session_factory,
                "livekit_dispatch_provider": provider,
                "verification_now": lambda: FIXED_NOW,
            },
            event,
        )

    assert exc_info.value.error_code == "dispatch_configuration"
    assert exc_info.value.retryable is False
    assert provider.list_calls == []
    assert provider.create_calls == []


@pytest.mark.anyio
@pytest.mark.parametrize("stale_case", ["expired", "reopened", "completed"])
async def test_stale_verification_state_never_calls_provider(
    db_session,
    stale_case: str,
) -> None:
    _user, activation, event = await _seed_verification_dispatch(db_session)
    if stale_case == "expired":
        activation.verification_window_expires_at = FIXED_NOW - timedelta(minutes=3)
    elif stale_case == "reopened":
        activation.verification_session_id = str(uuid4())
        activation.verification_status = "open"
    else:
        activation.verification_status = "succeeded"
    await db_session.commit()
    provider = _Provider()
    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    with pytest.raises(OutboxDeliveryError) as exc_info:
        await _handler()(
            {
                "session_factory": session_factory,
                "livekit_dispatch_provider": provider,
                "verification_now": lambda: FIXED_NOW,
            },
            event,
        )

    assert exc_info.value.error_code == "dispatch_ineligible"
    assert exc_info.value.retryable is False
    assert provider.list_calls == []
    assert provider.create_calls == []


@pytest.mark.anyio
async def test_claimed_verification_dispatch_rechecks_account_before_provider_io(
    db_session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, _activation, event = await _seed_verification_dispatch(db_session)
    provider = _Provider()
    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    real_snapshot = outbox_topics._verification_dispatch_snapshot

    async def snapshot_then_deactivate(*args, **kwargs):
        snapshot = await real_snapshot(*args, **kwargs)
        async with session_factory() as session:
            current_user = await session.get(User, user.id)
            assert current_user is not None
            current_user.status = "deactivating"
            await session.commit()
        return snapshot

    monkeypatch.setattr(
        outbox_topics,
        "_verification_dispatch_snapshot",
        snapshot_then_deactivate,
    )

    with pytest.raises(OutboxDeliveryError) as exc_info:
        await _handler()(
            {
                "session_factory": session_factory,
                "livekit_dispatch_provider": provider,
                "verification_now": lambda: FIXED_NOW,
            },
            event,
        )

    assert exc_info.value.error_code == "dispatch_ineligible"
    assert exc_info.value.retryable is False
    assert provider.list_calls == []
    assert provider.create_calls == []


@pytest.mark.anyio
async def test_verification_topic_is_registered_but_never_classified_as_call(
    db_session,
) -> None:
    _user, _activation, event = await _seed_verification_dispatch(db_session)

    assert "livekit.verification_dispatch" in SUPPORTED_OUTBOX_TOPICS
    assert outbox_topics.DEFAULT_OUTBOX_HANDLERS[
        "livekit.verification_dispatch"
    ] is _handler()
    assert _validated_event_call_id(event) is None
