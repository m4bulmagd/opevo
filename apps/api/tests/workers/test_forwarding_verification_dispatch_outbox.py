import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from presvo_contracts import (
    VERIFICATION_MESSAGE,
    ContractError,
    CustomerCallDispatch,
    ForwardingVerificationDispatch,
    create_contract,
    dump_contract,
    dump_contract_json,
    parse_dispatch,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.database import AsyncSessionFactory
from app.core.dispatch_token import DispatchTokenConfig
from app.core.provider_failures import ProviderFailure
from app.core.verification_token import verify_verification_token
from tests.dispatch_token_config import TEST_DISPATCH_TOKEN_CONFIG
from app.models.call import Call
from app.models.customer_activation import CustomerActivation
from app.models.outbox_event import OutboxEvent
from app.models.user import User
from app.providers.livekit_dispatch.base import LiveKitDispatch, LiveKitDispatchProvider
from app.services.outbox_service import OutboxService, SUPPORTED_OUTBOX_TOPICS
from app.workers.outbox.delivery import _validated_event_call_id
from app.workers.outbox.failures import OutboxDeliveryError
from app.workers.outbox import verification_dispatch
from app.workers.outbox.verification_dispatch import (
    deliver_livekit_verification_dispatch as _deliver_livekit_verification_dispatch_explicit,
)


FIXED_NOW = datetime(2026, 7, 18, 10, 0, tzinfo=UTC)
SUCCESS_MESSAGE = VERIFICATION_MESSAGE


def _fixed_now() -> datetime:
    return FIXED_NOW


async def _handler(
    event: OutboxEvent,
    *,
    session_factory: AsyncSessionFactory,
    provider: LiveKitDispatchProvider,
    token_config: DispatchTokenConfig = TEST_DISPATCH_TOKEN_CONFIG,
    livekit_agent_name: str = "ai-call-agent",
    now: Callable[[], datetime] = _fixed_now,
) -> None:
    await _deliver_livekit_verification_dispatch_explicit(
        event,
        session_factory=session_factory,
        provider=provider,
        token_config=token_config,
        livekit_agent_name=livekit_agent_name,
        now=now,
    )


def _reconciliation_snapshot(*, persisted_dispatch_id: str | None = None):
    return verification_dispatch._VerificationDispatchSnapshot(
        activation_id=UUID("00000000-0000-0000-0000-000000000021"),
        user_id=UUID("00000000-0000-0000-0000-000000000022"),
        session_id="00000000-0000-0000-0000-000000000023",
        room_name="verification-reconciliation-room",
        worker_name="verification-reconciliation-worker",
        metadata="",
        persisted_dispatch_id=persisted_dispatch_id,
    )


def _verification_reconciliation_metadata(snapshot) -> dict[str, object]:
    return dump_contract(
        create_contract(
            ForwardingVerificationDispatch,
            job_type="forwarding_verification",
            verification_session_id=UUID(snapshot.session_id),
            user_id=snapshot.user_id,
            agent_identity=f"agent-verification-{snapshot.session_id}",
            completion_token="private-token",
            message=SUCCESS_MESSAGE,
            tts_provider="speechmatics",
        )
    )


def _verification_reconciliation_dispatch(
    snapshot,
    metadata: str,
    *,
    dispatch_id: str = "reconciled-verification-dispatch",
) -> LiveKitDispatch:
    return LiveKitDispatch(
        id=dispatch_id,
        agent_name=snapshot.worker_name,
        room=snapshot.room_name,
        metadata=metadata,
        state="active",
    )


def test_verification_reconciliation_requires_a_valid_matching_verification_contract() -> (
    None
):
    snapshot = _reconciliation_snapshot()
    valid = _verification_reconciliation_metadata(snapshot)
    wrong_variant = dump_contract_json(
        create_contract(
            CustomerCallDispatch,
            job_type="customer_call",
            call_id=UUID("00000000-0000-0000-0000-000000000024"),
            user_id=snapshot.user_id,
            agent_config_id=UUID("00000000-0000-0000-0000-000000000025"),
            agent_identity="agent-call-test",
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
    invalid_metadata = {
        "malformed": "not-json",
        "unsupported_version": json.dumps(valid | {"schema_version": 2}),
        "wrong_variant": wrong_variant,
        "bad_uuid": json.dumps(valid | {"verification_session_id": "not-a-uuid"}),
        "mismatched_uuid": json.dumps(
            valid | {"verification_session_id": "00000000-0000-0000-0000-000000000026"}
        ),
        "mismatched_user_id": json.dumps(
            valid | {"user_id": "00000000-0000-0000-0000-000000000027"}
        ),
        "mismatched_agent_identity": json.dumps(
            valid | {"agent_identity": "FOREIGN_AGENT_IDENTITY_SENTINEL"}
        ),
    }

    reconciled = verification_dispatch._reconcile_verification_dispatches(
        snapshot,
        [_verification_reconciliation_dispatch(snapshot, json.dumps(valid))],
    )

    assert reconciled is not None
    assert reconciled.id == "reconciled-verification-dispatch"
    for metadata in invalid_metadata.values():
        with pytest.raises(OutboxDeliveryError) as caught:
            verification_dispatch._reconcile_verification_dispatches(
                snapshot,
                [_verification_reconciliation_dispatch(snapshot, metadata)],
            )
        assert caught.value.error_code == "dispatch_conflict"
        assert caught.value.retryable is False


def test_verification_reconciliation_rejects_mismatched_persisted_identity() -> None:
    snapshot = _reconciliation_snapshot(persisted_dispatch_id="persisted-other")
    with pytest.raises(OutboxDeliveryError) as caught:
        verification_dispatch._reconcile_verification_dispatches(
            snapshot,
            [
                _verification_reconciliation_dispatch(
                    snapshot,
                    json.dumps(_verification_reconciliation_metadata(snapshot)),
                )
            ],
        )

    assert caught.value.error_code == "dispatch_conflict"
    assert caught.value.retryable is False


@pytest.mark.anyio
async def test_verification_reconciliation_never_persists_foreign_agent_identity(
    db_session,
    caplog,
) -> None:
    user, activation, event = await _seed_verification_dispatch(db_session)
    activation_id = activation.id
    snapshot = verification_dispatch._VerificationDispatchSnapshot(
        activation_id=activation.id,
        user_id=user.id,
        session_id=activation.verification_session_id,
        room_name="verification-dispatch-room",
        worker_name="ai-call-agent",
        metadata="",
        persisted_dispatch_id=None,
    )
    metadata = _verification_reconciliation_metadata(snapshot) | {
        "agent_identity": "FOREIGN_AGENT_IDENTITY_SENTINEL"
    }
    provider = _Provider()
    provider.dispatches.append(
        _verification_reconciliation_dispatch(
            snapshot,
            json.dumps(metadata),
            dispatch_id="foreign-identity-dispatch",
        )
    )
    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    with caplog.at_level("INFO"), pytest.raises(OutboxDeliveryError) as caught:
        await _handler(
            event,
            session_factory=session_factory,
            provider=provider,
        )

    db_session.expire_all()
    stored = await db_session.get(CustomerActivation, activation_id)
    assert caught.value.error_code == "dispatch_conflict"
    assert caught.value.retryable is False
    assert stored is not None
    assert stored.verification_dispatch_id is None
    assert provider.create_calls == []
    assert "FOREIGN_AGENT_IDENTITY_SENTINEL" not in caplog.text


@pytest.mark.anyio
async def test_verification_empty_provider_dispatch_id_is_conflict_without_creation_or_persistence(
    db_session,
) -> None:
    user, activation, event = await _seed_verification_dispatch(db_session)
    activation_id = activation.id
    snapshot = verification_dispatch._VerificationDispatchSnapshot(
        activation_id=activation.id,
        user_id=user.id,
        session_id=activation.verification_session_id,
        room_name="verification-dispatch-room",
        worker_name="ai-call-agent",
        metadata="",
        persisted_dispatch_id=None,
    )
    empty_id_dispatch = _verification_reconciliation_dispatch(
        snapshot,
        json.dumps(_verification_reconciliation_metadata(snapshot)),
        dispatch_id="",
    )
    with pytest.raises(OutboxDeliveryError) as reconciliation_error:
        verification_dispatch._reconcile_verification_dispatches(
            snapshot,
            [empty_id_dispatch],
        )

    provider = _Provider()
    provider.dispatches.append(empty_id_dispatch)
    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    with pytest.raises(OutboxDeliveryError) as caught:
        await _handler(
            event,
            session_factory=session_factory,
            provider=provider,
        )

    db_session.expire_all()
    stored = await db_session.get(CustomerActivation, activation_id)
    assert reconciliation_error.value.error_code == "dispatch_conflict"
    assert reconciliation_error.value.retryable is False
    assert caught.value.error_code == "dispatch_conflict"
    assert caught.value.retryable is False
    assert stored is not None
    assert stored.verification_dispatch_id is None
    assert provider.create_calls == []


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
            raise ProviderFailure(
                provider="livekit",
                operation="list_dispatches",
                disposition="retryable",
                error_class="timeout",
            )
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
            raise ProviderFailure(
                provider="livekit",
                operation="create_dispatch",
                disposition="retryable",
                error_class="timeout",
            )
        created = LiveKitDispatch(
            id="verification-dispatch-1",
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


async def _seed_verification_dispatch(
    db_session,
    *,
    user_id: UUID | None = None,
    activation_id: UUID | None = None,
    session_id: str | None = None,
):
    user = User(
        id=user_id,
        clerk_user_id="verification-dispatch-owner",
        email="verification-dispatch-owner@example.invalid",
    )
    db_session.add(user)
    await db_session.flush()
    session_id = session_id or str(uuid4())
    activation = CustomerActivation(
        id=activation_id,
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
            "lifecycle_generation": user.lifecycle_generation,
        },
    )
    await db_session.commit()
    return user, activation, event


def test_verification_producer_forbids_extras_and_consumer_ignores_them() -> None:
    payload = {
        "job_type": "forwarding_verification",
        "verification_session_id": str(uuid4()),
        "user_id": str(uuid4()),
        "agent_identity": f"agent-verification-{uuid4()}",
        "completion_token": "scoped-token",
        "message": SUCCESS_MESSAGE,
        "tts_provider": "speechmatics",
    }

    metadata = create_contract(ForwardingVerificationDispatch, **payload)

    assert dump_contract(metadata) == payload | {"schema_version": 1}
    assert (
        create_contract(
            ForwardingVerificationDispatch,
            **(payload | {"tts_provider": "elevenlabs"}),
        ).tts_provider
        == "elevenlabs"
    )
    with pytest.raises(ContractError):
        create_contract(
            ForwardingVerificationDispatch,
            **(payload | {"system_prompt": "customer content"}),
        )
    parsed = parse_dispatch(
        dump_contract(metadata) | {"system_prompt": "customer content"}
    )
    assert "system_prompt" not in dump_contract(parsed)
    with pytest.raises(ContractError):
        create_contract(
            ForwardingVerificationDispatch,
            **(payload | {"job_type": "customer_call"}),
        )


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
    monkeypatch.setenv("LIVEKIT_AGENT_NAME", "ambient-worker-must-not-win")
    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    await _handler(
        event,
        session_factory=session_factory,
        provider=provider,
        livekit_agent_name="configured-worker",
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
    assert metadata["agent_identity"] == (f"agent-verification-{session_id}")
    assert metadata["message"] == SUCCESS_MESSAGE
    assert metadata["tts_provider"] == "speechmatics"
    assert set(metadata) == {
        "schema_version",
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
        config=TEST_DISPATCH_TOKEN_CONFIG,
    )
    assert metadata["completion_token"] not in json.dumps(outbox_payload)
    assert dump_contract(parse_dispatch(created["metadata"])) == metadata
    assert await db_session.scalar(select(func.count()).select_from(Call)) == 0


@pytest.mark.anyio
async def test_matching_provider_dispatch_reconciles_without_create(db_session) -> None:
    user, activation, event = await _seed_verification_dispatch(db_session)
    activation_id = activation.id
    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    provider = _Provider()
    provider.dispatches.append(
        LiveKitDispatch(
            id="verification-existing",
            agent_name="ai-call-agent",
            room="verification-dispatch-room",
            metadata=dump_contract_json(
                create_contract(
                    ForwardingVerificationDispatch,
                    job_type="forwarding_verification",
                    verification_session_id=UUID(activation.verification_session_id),
                    user_id=user.id,
                    agent_identity=(
                        f"agent-verification-{activation.verification_session_id}"
                    ),
                    completion_token="private-token",
                    message=SUCCESS_MESSAGE,
                    tts_provider="speechmatics",
                )
            ),
            state="active",
        )
    )

    await _handler(
        event,
        session_factory=session_factory,
        provider=provider,
    )

    db_session.expire_all()
    stored = await db_session.get(CustomerActivation, activation_id)
    assert stored is not None
    assert stored.verification_dispatch_id == "verification-existing"
    assert provider.create_calls == []


@pytest.mark.anyio
async def test_create_timeout_reconciles_to_one_verification_dispatch(
    db_session,
) -> None:
    _user, activation, event = await _seed_verification_dispatch(db_session)
    activation_id = activation.id
    provider = _Provider(timeout_after_create=True)
    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    await _handler(
        event,
        session_factory=session_factory,
        provider=provider,
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
    with pytest.raises(OutboxDeliveryError) as exc_info:
        await _handler(
            event,
            session_factory=session_factory,
            provider=provider,
        )
    await _handler(
        event,
        session_factory=session_factory,
        provider=provider,
    )

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

    await _handler(
        event,
        session_factory=session_factory,
        provider=provider,
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
        await _handler(
            event,
            session_factory=session_factory,
            provider=provider,
        )

    assert exc_info.value.error_code == "dispatch_conflict"
    assert exc_info.value.retryable is False
    assert provider.create_calls == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    "invalid_case", ["topic", "aggregate", "activation", "session"]
)
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
        await _handler(
            event,
            session_factory=session_factory,
            provider=provider,
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
        await _handler(
            event,
            session_factory=session_factory,
            provider=provider,
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
    real_snapshot = verification_dispatch._verification_dispatch_snapshot

    async def snapshot_then_deactivate(*args, **kwargs):
        snapshot = await real_snapshot(*args, **kwargs)
        async with session_factory() as session:
            current_user = await session.get(User, user.id)
            assert current_user is not None
            current_user.status = "deactivating"
            await session.commit()
        return snapshot

    monkeypatch.setattr(
        verification_dispatch,
        "_verification_dispatch_snapshot",
        snapshot_then_deactivate,
    )

    with pytest.raises(OutboxDeliveryError) as exc_info:
        await _handler(
            event,
            session_factory=session_factory,
            provider=provider,
        )

    assert exc_info.value.error_code == "dispatch_ineligible"
    assert exc_info.value.retryable is False
    assert provider.list_calls == []
    assert provider.create_calls == []


@pytest.mark.anyio
async def test_stale_account_generation_never_dispatches_verification(
    db_session,
) -> None:
    user, _activation, event = await _seed_verification_dispatch(db_session)
    user.lifecycle_generation = 2
    event.payload = event.payload | {"lifecycle_generation": 1}
    await db_session.commit()
    provider = _Provider()
    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    with pytest.raises(OutboxDeliveryError) as exc_info:
        await _handler(
            event,
            session_factory=session_factory,
            provider=provider,
        )

    assert exc_info.value.error_code == "dispatch_ineligible"
    assert exc_info.value.retryable is False
    assert provider.list_calls == []
    assert provider.create_calls == []


@pytest.mark.anyio
async def test_deactivation_during_verification_reconciliation_prevents_create(
    db_session,
) -> None:
    user, _activation, event = await _seed_verification_dispatch(db_session)
    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    class _DeactivatingListProvider(_Provider):
        async def list_dispatches(
            self,
            *,
            room_name: str,
        ) -> list[LiveKitDispatch]:
            async with session_factory() as session:
                current_user = await session.get(User, user.id)
                assert current_user is not None
                current_user.status = "deactivating"
                current_user.lifecycle_generation += 1
                await session.commit()
            return await super().list_dispatches(room_name=room_name)

    provider = _DeactivatingListProvider()

    with pytest.raises(OutboxDeliveryError) as exc_info:
        await _handler(
            event,
            session_factory=session_factory,
            provider=provider,
        )

    assert exc_info.value.error_code == "dispatch_ineligible"
    assert exc_info.value.retryable is False
    assert provider.list_calls == ["verification-dispatch-room"]
    assert provider.create_calls == []


@pytest.mark.anyio
async def test_verification_topic_is_registered_but_never_classified_as_call(
    db_session,
) -> None:
    _user, _activation, event = await _seed_verification_dispatch(db_session)

    assert "livekit.verification_dispatch" in SUPPORTED_OUTBOX_TOPICS
    assert _validated_event_call_id(event) is None
