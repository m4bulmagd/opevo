import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock
from uuid import UUID

import pytest
import pytest_asyncio
import telnyx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.models import Base
from app.models.account_deactivation_operation import AccountDeactivationOperation
from app.models.agent_config import AgentConfig
from app.models.business_profile import BusinessProfile
from app.models.call import Call
from app.models.customer_activation import CustomerActivation
from app.models.outbox_event import OutboxEvent
from app.models.phone_number import PhoneNumber
from app.models.phone_number_provisioning import PhoneNumberProvisioning
from app.models.subscription import Subscription
from app.models.usage_ledger import UsageLedger
from app.models.user import User
from app.core.provider_failures import ProviderFailure
from app.providers.telephony.telnyx import TelephonyTelnyx
from app.repositories.account_deactivation_repository import (
    AccountDeactivationRepository,
)
from app.services.account_lifecycle_service import AccountLifecycleService
from app.workers.outbox import account_deactivation as account_deactivation_module
from app.workers.outbox.account_deactivation import deliver_account_deactivation
from app.workers.outbox.failures import OutboxDeliveryError


NOW = datetime(2026, 7, 25, 10, 0, tzinfo=UTC)
PRIVATE_PHONE_PROVIDER_ID = "pn_PROVIDER_PRIVATE_91"
PRIVATE_SUBSCRIPTION_ID = "sub_PROVIDER_PRIVATE_42"
PRIVATE_E164 = "+33987654321"
PRIVATE_CONTENT = "CUSTOMER_CONTENT_PRIVATE"


class TrackingSessionFactory:
    def __init__(self, delegate: async_sessionmaker[AsyncSession]) -> None:
        self.delegate = delegate
        self.sessions: list[AsyncSession] = []

    @asynccontextmanager
    async def __call__(self) -> AsyncIterator[AsyncSession]:
        async with self.delegate() as session:
            self.sessions.append(session)
            yield session

    def assert_no_active_transaction(self) -> None:
        assert all(not session.in_transaction() for session in self.sessions)


class RecordingTelephonyProvider:
    def __init__(
        self,
        calls: list[tuple[str, str]],
        assert_no_transaction: Callable[[], None],
        *,
        failure: tuple[str, Exception] | None = None,
    ) -> None:
        self.calls = calls
        self.assert_no_transaction = assert_no_transaction
        self.failure = failure

    async def disable_number(self, *, provider_number_id: str) -> str:
        self.assert_no_transaction()
        self.calls.append(("telephony.disable", provider_number_id))
        self._maybe_fail("telephony.disable")
        return "app-disabled"

    async def release_number(self, *, provider_number_id: str) -> None:
        self.assert_no_transaction()
        self.calls.append(("telephony.release", provider_number_id))
        self._maybe_fail("telephony.release")

    def _maybe_fail(self, operation: str) -> None:
        if self.failure is not None and self.failure[0] == operation:
            raise self.failure[1]


class RecordingSubscriptionProvider:
    def __init__(
        self,
        calls: list[tuple[str, str]],
        assert_no_transaction: Callable[[], None],
        *,
        failure: Exception | None = None,
    ) -> None:
        self.calls = calls
        self.assert_no_transaction = assert_no_transaction
        self.failure = failure

    async def cancel_immediately(self, subscription_id: str) -> None:
        self.assert_no_transaction()
        self.calls.append(("subscription.cancel", subscription_id))
        if self.failure is not None:
            raise self.failure


class RecordingObservability:
    def __init__(self) -> None:
        self.results: list[tuple[str, str, str, str]] = []
        self.attention: list[tuple[str, str, str]] = []
        self.completions: list[tuple[str, float]] = []

    def record_account_deactivation_result(
        self,
        trigger: str,
        step: str,
        outcome: str,
        error_class: str,
    ) -> None:
        self.results.append((trigger, step, outcome, error_class))

    def record_account_deactivation_attention(
        self,
        trigger: str,
        step: str,
        error_class: str,
    ) -> None:
        self.attention.append((trigger, step, error_class))

    def record_account_deactivation_completion(
        self,
        trigger: str,
        duration_seconds: float,
    ) -> None:
        self.completions.append((trigger, duration_seconds))


@dataclass(frozen=True)
class SeededOperation:
    operation_id: UUID
    user_id: UUID
    phone_number_id: UUID
    call_id: UUID
    activation_id: UUID


@pytest_asyncio.fixture
async def deactivation_session_factory(
    tmp_path: Path,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    database_path = tmp_path / "account-deactivation.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}", future=True)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    yield session_factory
    await engine.dispose()


async def _seed_operation(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    trigger: str = "owner_request",
    active_call: bool = False,
    completed_steps: int = 0,
) -> SeededOperation:
    async with session_factory() as session:
        user = User(
            clerk_user_id=f"deactivation-{completed_steps}-{trigger}-{active_call}",
            email=f"deactivation-{completed_steps}-{trigger}-{active_call}@example.com",
            status="deactivating",
            lifecycle_generation=2,
            country_code="FR",
        )
        session.add(user)
        await session.flush()
        phone = PhoneNumber(
            user_id=user.id,
            e164=PRIVATE_E164,
            country_code="FR",
            provider="telnyx",
            provider_number_id=PRIVATE_PHONE_PROVIDER_ID,
            provider_connection_name="app-disabled",
            is_active=False,
        )
        activation = CustomerActivation(
            user_id=user.id,
            profile_confirmed_revision=7,
            profile_confirmed_at=NOW - timedelta(days=3),
            provisioning_consented_at=NOW - timedelta(days=2),
            provisioning_idempotency_key=f"private-provisioning-key-{user.id}",
            verification_window_started_at=NOW - timedelta(hours=2),
            verification_window_expires_at=NOW + timedelta(hours=2),
            verification_session_id=f"private-verification-session-{user.id}",
            verification_claimed_at=NOW - timedelta(hours=1),
            verification_dispatch_id=f"private-dispatch-{user.id}",
            verification_routing_fingerprint="a" * 64,
            verification_status="succeeded",
            verified_routing_fingerprint="b" * 64,
            forwarding_verified_at=NOW - timedelta(hours=1),
            go_live_requested_at=NOW - timedelta(minutes=30),
            go_live_approved_at=NOW - timedelta(minutes=20),
            activated_at=NOW - timedelta(minutes=10),
            last_failure_code="routing_provider_terminal",
        )
        session.add_all(
            [
                phone,
                activation,
                AgentConfig(
                    user_id=user.id,
                    agent_name="Léa",
                    owner_context=PRIVATE_CONTENT,
                    system_prompt=PRIVATE_CONTENT,
                    knowledge_base=PRIVATE_CONTENT,
                    is_enabled=False,
                ),
                BusinessProfile(
                    user_id=user.id,
                    owner_name="Camille",
                    business_name=PRIVATE_CONTENT,
                    public_description=PRIVATE_CONTENT,
                    existing_phone_e164="+33611111111",
                    detected_carrier="orange",
                    detected_number_type="mobile",
                    carrier_lookup_status="confirmed",
                    carrier_looked_up_at=NOW - timedelta(days=4),
                    confirmed_carrier="orange",
                    receptionist_name="Léa",
                    special_instructions=PRIVATE_CONTENT,
                ),
                Subscription(
                    user_id=user.id,
                    stripe_customer_id=f"cus-private-{user.id}",
                    stripe_subscription_id=PRIVATE_SUBSCRIPTION_ID,
                    plan_tier="starter",
                    status="canceled" if trigger == "subscription_ended" else "active",
                    allocated_minutes=60,
                    cancel_at_period_end=True,
                    cancellation_effective_at=NOW + timedelta(days=2),
                    lifecycle_generation=1,
                ),
            ]
        )
        await session.flush()
        call = Call(
            user_id=user.id,
            phone_number_id=phone.id,
            status="connected" if active_call else "completed",
            caller_number="+33622222222",
            summary_text=PRIVATE_CONTENT,
            recording_object_key=f"recordings/{PRIVATE_CONTENT}",
        )
        session.add(call)
        await session.flush()
        session.add_all(
            [
                PhoneNumberProvisioning(
                    user_id=user.id,
                    phone_number_id=phone.id,
                    target_country_code="FR",
                    status="succeeded",
                    attempt_count=1,
                    provider_operation_key=f"private-operation-key-{user.id}",
                ),
                UsageLedger(
                    user_id=user.id,
                    call_id=call.id,
                    event_type="call_completed",
                    source_id=f"private-usage-{user.id}",
                    minutes_delta=-1,
                    balance_after=59,
                ),
            ]
        )
        operation = AccountDeactivationOperation(
            user_id=user.id,
            lifecycle_generation=2,
            trigger=trigger,
            status="pending",
            stripe_subscription_id=PRIVATE_SUBSCRIPTION_ID,
            phone_provider_id=PRIVATE_PHONE_PROVIDER_ID,
            requested_at=NOW - timedelta(minutes=5),
        )
        step_names = (
            "routing_disabled_at",
            "subscription_canceled_at",
            "active_call_drained_at",
            "number_released_at",
            "activation_reset_at",
            "completed_at",
        )
        for step_name in step_names[:completed_steps]:
            setattr(operation, step_name, NOW - timedelta(minutes=1))
        if completed_steps == len(step_names):
            operation.status = "completed"
            user.status = "inactive"
        session.add(operation)
        await session.commit()
        return SeededOperation(
            operation_id=operation.id,
            user_id=user.id,
            phone_number_id=phone.id,
            call_id=call.id,
            activation_id=activation.id,
        )


def _event(operation_id: UUID, *, payload: dict[str, Any] | None = None) -> OutboxEvent:
    return OutboxEvent(
        topic="account.deactivate",
        aggregate_type="account-deactivation-operation",
        aggregate_id=operation_id,
        idempotency_key=f"account.deactivate:{operation_id}",
        payload=payload or {"operation_id": str(operation_id)},
        next_attempt_at=NOW,
    )


def _ctx(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    calls: list[tuple[str, str]] | None = None,
    telephony_failure: tuple[str, Exception] | None = None,
    subscription_failure: Exception | None = None,
) -> tuple[dict[str, Any], list[tuple[str, str]], TrackingSessionFactory]:
    provider_calls = calls if calls is not None else []
    tracking_factory = TrackingSessionFactory(session_factory)
    telemetry = RecordingObservability()
    return (
        {
            "session_factory": tracking_factory,
            "telephony_provider": RecordingTelephonyProvider(
                provider_calls,
                tracking_factory.assert_no_active_transaction,
                failure=telephony_failure,
            ),
            "subscription_provider": RecordingSubscriptionProvider(
                provider_calls,
                tracking_factory.assert_no_active_transaction,
                failure=subscription_failure,
            ),
            "observability": telemetry,
            "account_deactivation_now": lambda: NOW,
        },
        provider_calls,
        tracking_factory,
    )


@pytest.mark.anyio
async def test_owner_request_runs_strict_provider_order_and_preserves_history(
    deactivation_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    seeded = await _seed_operation(deactivation_session_factory)
    ctx, provider_calls, _ = _ctx(deactivation_session_factory)

    await deliver_account_deactivation(ctx, _event(seeded.operation_id))

    assert provider_calls == [
        ("telephony.disable", PRIVATE_PHONE_PROVIDER_ID),
        ("subscription.cancel", PRIVATE_SUBSCRIPTION_ID),
        ("telephony.release", PRIVATE_PHONE_PROVIDER_ID),
    ]
    async with deactivation_session_factory() as session:
        operation = await session.get(AccountDeactivationOperation, seeded.operation_id)
        user = await session.get(User, seeded.user_id)
        call = await session.get(Call, seeded.call_id)
        activation = await session.get(CustomerActivation, seeded.activation_id)
        agent = await session.scalar(
            select(AgentConfig).where(AgentConfig.user_id == seeded.user_id)
        )
        profile = await session.scalar(
            select(BusinessProfile).where(BusinessProfile.user_id == seeded.user_id)
        )
        subscription = await session.scalar(
            select(Subscription).where(Subscription.user_id == seeded.user_id)
        )
        assert operation is not None
        assert user is not None
        assert call is not None
        assert activation is not None
        assert agent is not None
        assert profile is not None
        assert subscription is not None
        assert operation.status == "completed"
        assert all(
            getattr(operation, name) is not None
            for name in (
                "routing_disabled_at",
                "subscription_canceled_at",
                "active_call_drained_at",
                "number_released_at",
                "activation_reset_at",
                "completed_at",
            )
        )
        assert user.status == "inactive"
        assert subscription.status == "canceled"
        assert subscription.cancel_at_period_end is False
        assert subscription.cancellation_effective_at is not None
        assert call.phone_number_id is None
        assert call.summary_text == PRIVATE_CONTENT
        assert call.recording_object_key == f"recordings/{PRIVATE_CONTENT}"
        assert agent.owner_context == PRIVATE_CONTENT
        assert agent.system_prompt == PRIVATE_CONTENT
        assert agent.knowledge_base == PRIVATE_CONTENT
        assert agent.is_enabled is False
        assert profile.business_name == PRIVATE_CONTENT
        assert profile.special_instructions == PRIVATE_CONTENT
        assert profile.detected_carrier == "orange"
        assert profile.detected_number_type == "mobile"
        assert profile.carrier_lookup_status == "confirmed"
        assert profile.carrier_looked_up_at is not None
        assert profile.carrier_looked_up_at.replace(tzinfo=UTC) == (
            NOW - timedelta(days=4)
        )
        assert profile.confirmed_carrier == "orange"
        assert activation.profile_confirmed_revision == 7
        assert activation.profile_confirmed_at is not None
        assert activation.verification_status == "not_started"
        for name in (
            "provisioning_consented_at",
            "provisioning_idempotency_key",
            "verification_window_started_at",
            "verification_window_expires_at",
            "verification_session_id",
            "verification_claimed_at",
            "verification_dispatch_id",
            "verification_routing_fingerprint",
            "verified_routing_fingerprint",
            "forwarding_verified_at",
            "go_live_requested_at",
            "go_live_approved_at",
            "activated_at",
            "last_failure_code",
        ):
            assert getattr(activation, name) is None
        assert await session.get(PhoneNumber, seeded.phone_number_id) is None
        assert (
            await session.scalar(
                select(func.count())
                .select_from(PhoneNumberProvisioning)
                .where(PhoneNumberProvisioning.user_id == seeded.user_id)
            )
            == 0
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(UsageLedger)
                .where(UsageLedger.user_id == seeded.user_id)
            )
            == 1
        )


@pytest.mark.anyio
async def test_exact_telnyx_disable_404_continues_release_and_reset(
    deactivation_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    seeded = await _seed_operation(deactivation_session_factory)
    phone_number = MagicMock()
    phone_number.delete.return_value = {
        "data": {
            "id": PRIVATE_PHONE_PROVIDER_ID,
            "status": "deleted",
        }
    }
    phone_number_resource = MagicMock()
    phone_number_resource.modify.side_effect = telnyx.error.ResourceNotFoundError(
        [{"title": "private provider response"}],
        http_status=404,
    )
    phone_number_resource.return_value = phone_number
    telephony_provider = TelephonyTelnyx(
        api_key="KEY",
        disabled_connection_id="disabled",
        phone_number_resource=phone_number_resource,
    )
    tracking_factory = TrackingSessionFactory(deactivation_session_factory)
    subscription_calls: list[tuple[str, str]] = []
    ctx = {
        "session_factory": tracking_factory,
        "telephony_provider": telephony_provider,
        "subscription_provider": RecordingSubscriptionProvider(
            subscription_calls,
            tracking_factory.assert_no_active_transaction,
        ),
        "observability": RecordingObservability(),
        "account_deactivation_now": lambda: NOW,
    }

    await deliver_account_deactivation(ctx, _event(seeded.operation_id))

    phone_number_resource.modify.assert_called_once_with(
        PRIVATE_PHONE_PROVIDER_ID,
        api_key="KEY",
        connection_id="disabled",
    )
    phone_number_resource.assert_called_once_with(
        PRIVATE_PHONE_PROVIDER_ID,
        api_key="KEY",
    )
    phone_number.delete.assert_called_once_with()
    assert subscription_calls == [
        ("subscription.cancel", PRIVATE_SUBSCRIPTION_ID),
    ]
    async with deactivation_session_factory() as session:
        operation = await session.get(
            AccountDeactivationOperation,
            seeded.operation_id,
        )
        user = await session.get(User, seeded.user_id)
        assert operation is not None and operation.status == "completed"
        assert operation.routing_disabled_at is not None
        assert operation.number_released_at is not None
        assert operation.activation_reset_at is not None
        assert user is not None and user.status == "inactive"
        assert await session.get(PhoneNumber, seeded.phone_number_id) is None


@pytest.mark.anyio
async def test_subscription_ended_never_calls_subscription_provider(
    deactivation_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    seeded = await _seed_operation(
        deactivation_session_factory,
        trigger="subscription_ended",
    )
    ctx, provider_calls, _ = _ctx(deactivation_session_factory)

    await deliver_account_deactivation(ctx, _event(seeded.operation_id))

    assert provider_calls == [
        ("telephony.disable", PRIVATE_PHONE_PROVIDER_ID),
        ("telephony.release", PRIVATE_PHONE_PROVIDER_ID),
    ]


@pytest.mark.anyio
async def test_real_subscription_ended_request_disables_before_drain_and_release(
    deactivation_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with deactivation_session_factory() as session:
        user = User(
            clerk_user_id="real-subscription-ended",
            email="real-subscription-ended@example.com",
            status="active",
        )
        session.add(user)
        await session.flush()
        phone = PhoneNumber(
            user_id=user.id,
            e164=PRIVATE_E164,
            country_code="FR",
            provider="telnyx",
            provider_number_id=PRIVATE_PHONE_PROVIDER_ID,
            provider_connection_name="app-active",
            is_active=True,
        )
        session.add_all(
            [
                phone,
                AgentConfig(user_id=user.id, is_enabled=True),
                Subscription(
                    user_id=user.id,
                    stripe_customer_id="cus-real-subscription-ended",
                    stripe_subscription_id=PRIVATE_SUBSCRIPTION_ID,
                    plan_tier="starter",
                    status="canceled",
                    allocated_minutes=0,
                ),
            ]
        )
        await session.flush()
        call = Call(
            user_id=user.id,
            phone_number_id=phone.id,
            status="connected",
        )
        session.add(call)
        await session.commit()
        user_id = user.id
        call_id = call.id

    async with deactivation_session_factory() as session:
        operation = await AccountLifecycleService(
            session, activation_flow_enabled=False
        ).request_in_transaction(
            user_id,
            trigger="subscription_ended",
            stripe_subscription_id=PRIVATE_SUBSCRIPTION_ID,
        )
        assert operation is not None
        await session.commit()
        operation_id = operation.id
        event = await session.scalar(
            select(OutboxEvent).where(
                OutboxEvent.aggregate_id == operation_id,
                OutboxEvent.topic == "account.deactivate",
            )
        )
        assert event is not None
        assert event.payload == {"operation_id": str(operation_id)}
        assert operation.routing_disabled_at is None
        assert operation.subscription_canceled_at is None

    ctx, provider_calls, _ = _ctx(deactivation_session_factory)
    with pytest.raises(OutboxDeliveryError) as exc_info:
        await deliver_account_deactivation(ctx, event)

    assert exc_info.value.error_code == "account_call_draining"
    assert provider_calls == [
        ("telephony.disable", PRIVATE_PHONE_PROVIDER_ID),
    ]
    async with deactivation_session_factory() as session:
        operation = await session.get(AccountDeactivationOperation, operation_id)
        call = await session.get(Call, call_id)
        assert operation is not None
        assert call is not None
        assert operation.routing_disabled_at is not None
        assert operation.subscription_canceled_at is not None
        assert operation.active_call_drained_at is None
        assert operation.number_released_at is None
        call.status = "completed"
        await session.commit()

    await deliver_account_deactivation(ctx, event)

    assert provider_calls == [
        ("telephony.disable", PRIVATE_PHONE_PROVIDER_ID),
        ("telephony.release", PRIVATE_PHONE_PROVIDER_ID),
    ]


@pytest.mark.parametrize("trigger", ["owner_request", "subscription_ended"])
@pytest.mark.anyio
async def test_terminal_local_subscription_satisfies_step_without_identity_match(
    deactivation_session_factory: async_sessionmaker[AsyncSession],
    trigger: str,
) -> None:
    seeded = await _seed_operation(
        deactivation_session_factory,
        trigger=trigger,
    )
    async with deactivation_session_factory() as session:
        subscription = await session.scalar(
            select(Subscription).where(Subscription.user_id == seeded.user_id)
        )
        assert subscription is not None
        subscription.status = "canceled"
        subscription.stripe_subscription_id = "sub-terminal-replacement"
        await session.commit()
    ctx, provider_calls, _ = _ctx(deactivation_session_factory)

    await deliver_account_deactivation(ctx, _event(seeded.operation_id))

    assert ("subscription.cancel", PRIVATE_SUBSCRIPTION_ID) not in provider_calls
    async with deactivation_session_factory() as session:
        operation = await session.get(AccountDeactivationOperation, seeded.operation_id)
        assert operation is not None
        assert operation.status == "completed"


@pytest.mark.anyio
async def test_active_call_commits_progress_and_retries_without_exhaustion(
    deactivation_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    seeded = await _seed_operation(deactivation_session_factory, active_call=True)
    ctx, provider_calls, _ = _ctx(deactivation_session_factory)

    with pytest.raises(OutboxDeliveryError) as exc_info:
        await deliver_account_deactivation(ctx, _event(seeded.operation_id))

    assert exc_info.value.error_code == "account_call_draining"
    assert exc_info.value.retryable is True
    assert exc_info.value.exhaustible is False
    assert provider_calls == [
        ("telephony.disable", PRIVATE_PHONE_PROVIDER_ID),
        ("subscription.cancel", PRIVATE_SUBSCRIPTION_ID),
    ]
    async with deactivation_session_factory() as session:
        operation = await session.get(AccountDeactivationOperation, seeded.operation_id)
        assert operation is not None
        assert operation.routing_disabled_at is not None
        assert operation.subscription_canceled_at is not None
        assert operation.active_call_drained_at is None
        assert operation.number_released_at is None
        assert operation.last_error_code == "account_call_draining"
        call = await session.get(Call, seeded.call_id)
        assert call is not None
        call.status = "completed"
        await session.commit()

    await deliver_account_deactivation(ctx, _event(seeded.operation_id))

    assert provider_calls == [
        ("telephony.disable", PRIVATE_PHONE_PROVIDER_ID),
        ("subscription.cancel", PRIVATE_SUBSCRIPTION_ID),
        ("telephony.release", PRIVATE_PHONE_PROVIDER_ID),
    ]
    async with deactivation_session_factory() as session:
        operation = await session.get(AccountDeactivationOperation, seeded.operation_id)
        assert operation is not None
        assert operation.status == "completed"
        assert operation.last_error_code is None


@pytest.mark.parametrize(
    ("completed_steps", "expected_calls"),
    [
        (
            0,
            [
                ("telephony.disable", PRIVATE_PHONE_PROVIDER_ID),
                ("subscription.cancel", PRIVATE_SUBSCRIPTION_ID),
                ("telephony.release", PRIVATE_PHONE_PROVIDER_ID),
            ],
        ),
        (
            1,
            [
                ("subscription.cancel", PRIVATE_SUBSCRIPTION_ID),
                ("telephony.release", PRIVATE_PHONE_PROVIDER_ID),
            ],
        ),
        (2, [("telephony.release", PRIVATE_PHONE_PROVIDER_ID)]),
        (3, [("telephony.release", PRIVATE_PHONE_PROVIDER_ID)]),
        (4, []),
        (5, []),
        (6, []),
    ],
)
@pytest.mark.anyio
async def test_restart_from_each_committed_timestamp_skips_completed_provider_work(
    deactivation_session_factory: async_sessionmaker[AsyncSession],
    completed_steps: int,
    expected_calls: list[tuple[str, str]],
) -> None:
    seeded = await _seed_operation(
        deactivation_session_factory,
        completed_steps=completed_steps,
    )
    ctx, provider_calls, _ = _ctx(deactivation_session_factory)

    await deliver_account_deactivation(ctx, _event(seeded.operation_id))

    assert provider_calls == expected_calls
    async with deactivation_session_factory() as session:
        operation = await session.get(AccountDeactivationOperation, seeded.operation_id)
        assert operation is not None
        assert operation.status == "completed"


@pytest.mark.parametrize(
    ("telephony_failure", "subscription_failure", "expected_calls"),
    [
        (
            (
                "telephony.disable",
                ProviderFailure(
                    provider="telnyx",
                    operation="disable_number",
                    disposition="retryable",
                    error_class="rate_limited",
                ),
            ),
            None,
            [("telephony.disable", PRIVATE_PHONE_PROVIDER_ID)],
        ),
        (
            None,
            ProviderFailure(
                provider="stripe",
                operation="cancel_subscription",
                disposition="retryable",
                error_class="timeout",
            ),
            [
                ("telephony.disable", PRIVATE_PHONE_PROVIDER_ID),
                ("subscription.cancel", PRIVATE_SUBSCRIPTION_ID),
            ],
        ),
        (
            (
                "telephony.release",
                ProviderFailure(
                    provider="telnyx",
                    operation="release_number",
                    disposition="retryable",
                    error_class="unavailable",
                ),
            ),
            None,
            [
                ("telephony.disable", PRIVATE_PHONE_PROVIDER_ID),
                ("subscription.cancel", PRIVATE_SUBSCRIPTION_ID),
                ("telephony.release", PRIVATE_PHONE_PROVIDER_ID),
            ],
        ),
    ],
)
@pytest.mark.anyio
async def test_retryable_provider_failures_are_non_exhausting_and_committed(
    deactivation_session_factory: async_sessionmaker[AsyncSession],
    telephony_failure: tuple[str, Exception] | None,
    subscription_failure: Exception | None,
    expected_calls: list[tuple[str, str]],
) -> None:
    seeded = await _seed_operation(deactivation_session_factory)
    ctx, provider_calls, _ = _ctx(
        deactivation_session_factory,
        telephony_failure=telephony_failure,
        subscription_failure=subscription_failure,
    )

    with pytest.raises(OutboxDeliveryError) as exc_info:
        await deliver_account_deactivation(ctx, _event(seeded.operation_id))

    assert exc_info.value.error_code == "provider_retryable"
    assert exc_info.value.retryable is True
    assert exc_info.value.exhaustible is False
    assert provider_calls == expected_calls
    async with deactivation_session_factory() as session:
        operation = await session.get(AccountDeactivationOperation, seeded.operation_id)
        user = await session.get(User, seeded.user_id)
        assert operation is not None
        assert user is not None
        assert operation.status == "processing"
        assert operation.last_error_code == "provider_retryable"
        assert user.status == "deactivating"


@pytest.mark.parametrize(
    (
        "completed_steps",
        "telephony_failure",
        "subscription_failure",
        "expected_code",
    ),
    [
        (
            0,
            (
                "telephony.disable",
                ProviderFailure(
                    provider="telnyx",
                    operation="disable_number",
                    disposition="terminal",
                    error_class="authentication",
                ),
            ),
            None,
            "telephony_authentication",
        ),
        (
            0,
            (
                "telephony.disable",
                ProviderFailure(
                    provider="telnyx",
                    operation="disable_number",
                    disposition="terminal",
                    error_class="validation",
                ),
            ),
            None,
            "provider_contract",
        ),
        (
            1,
            None,
            ProviderFailure(
                provider="stripe",
                operation="cancel_subscription",
                disposition="terminal",
                error_class="authentication",
            ),
            "subscription_authentication",
        ),
        (
            1,
            None,
            ProviderFailure(
                provider="stripe",
                operation="cancel_subscription",
                disposition="terminal",
                error_class="validation",
            ),
            "subscription_contract",
        ),
        (
            1,
            None,
            ProviderFailure(
                provider="fake",
                operation="validate",
                disposition="terminal",
                error_class="validation",
            ),
            "subscription_contract",
        ),
        (
            3,
            (
                "telephony.release",
                ProviderFailure(
                    provider="telnyx",
                    operation="release_number",
                    disposition="terminal",
                    error_class="conflict",
                ),
            ),
            None,
            "telephony_release_conflict",
        ),
    ],
)
@pytest.mark.anyio
async def test_terminal_provider_failure_commits_safe_attention_before_error(
    deactivation_session_factory: async_sessionmaker[AsyncSession],
    completed_steps: int,
    telephony_failure: tuple[str, Exception] | None,
    subscription_failure: Exception | None,
    expected_code: str,
) -> None:
    seeded = await _seed_operation(
        deactivation_session_factory,
        completed_steps=completed_steps,
    )
    ctx, _, _ = _ctx(
        deactivation_session_factory,
        telephony_failure=telephony_failure,
        subscription_failure=subscription_failure,
    )

    with pytest.raises(OutboxDeliveryError) as exc_info:
        await deliver_account_deactivation(ctx, _event(seeded.operation_id))

    assert exc_info.value.error_code == expected_code
    assert exc_info.value.retryable is False
    assert exc_info.value.exhaustible is True
    async with deactivation_session_factory() as session:
        operation = await session.get(AccountDeactivationOperation, seeded.operation_id)
        user = await session.get(User, seeded.user_id)
        assert operation is not None
        assert user is not None
        assert operation.status == "attention_required"
        assert operation.last_error_code == expected_code
        assert user.status == "deactivating"


@pytest.mark.anyio
async def test_handler_rejects_non_reference_payload_without_provider_work(
    deactivation_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    seeded = await _seed_operation(deactivation_session_factory)
    ctx, provider_calls, _ = _ctx(deactivation_session_factory)

    with pytest.raises(OutboxDeliveryError) as exc_info:
        await deliver_account_deactivation(
            ctx,
            _event(
                seeded.operation_id,
                payload={
                    "operation_id": str(seeded.operation_id),
                    "provider_id": PRIVATE_PHONE_PROVIDER_ID,
                },
            ),
        )

    assert exc_info.value.error_code == "invalid_payload"
    assert provider_calls == []


@pytest.mark.anyio
async def test_logs_payload_and_observability_exclude_private_values(
    deactivation_session_factory: async_sessionmaker[AsyncSession],
    caplog: pytest.LogCaptureFixture,
) -> None:
    seeded = await _seed_operation(deactivation_session_factory)
    ctx, _, _ = _ctx(deactivation_session_factory)
    caplog.set_level(logging.DEBUG)
    event = _event(seeded.operation_id)

    await deliver_account_deactivation(ctx, event)

    telemetry = ctx["observability"]
    exported = repr(
        {
            "payload": event.payload,
            "logs": caplog.text,
            "results": telemetry.results,
            "attention": telemetry.attention,
            "completions": telemetry.completions,
        }
    )
    assert event.payload == {"operation_id": str(seeded.operation_id)}
    for private_value in (
        PRIVATE_PHONE_PROVIDER_ID,
        PRIVATE_SUBSCRIPTION_ID,
        PRIVATE_E164,
        PRIVATE_CONTENT,
        "private-provisioning-key",
        "private-verification-session",
    ):
        assert private_value not in exported


@pytest.mark.anyio
async def test_terminal_failure_redacts_raw_cause_from_customer_projection_and_logs(
    deactivation_session_factory: async_sessionmaker[AsyncSession],
    caplog: pytest.LogCaptureFixture,
) -> None:
    seeded = await _seed_operation(deactivation_session_factory)
    raw_error = RuntimeError(
        f"RAW_ERROR {PRIVATE_PHONE_PROVIDER_ID} {PRIVATE_E164} {PRIVATE_CONTENT}"
    )
    terminal_error = ProviderFailure(
        provider="telnyx",
        operation="disable_number",
        disposition="terminal",
        error_class="authentication",
    )
    terminal_error.__cause__ = raw_error
    ctx, _, _ = _ctx(
        deactivation_session_factory,
        telephony_failure=("telephony.disable", terminal_error),
    )
    caplog.set_level(logging.DEBUG)

    with pytest.raises(OutboxDeliveryError):
        await deliver_account_deactivation(ctx, _event(seeded.operation_id))

    async with deactivation_session_factory() as session:
        projection = await AccountLifecycleService(
            session, activation_flow_enabled=False
        ).get_account(seeded.user_id)
    exported = (
        f"{projection.model_dump()} {caplog.text!r} {ctx['observability'].results!r}"
    )
    assert projection.blocker == "deactivation_attention_required"
    for private_value in (
        "RAW_ERROR",
        PRIVATE_PHONE_PROVIDER_ID,
        PRIVATE_E164,
        PRIVATE_CONTENT,
    ):
        assert private_value not in exported


@pytest.mark.anyio
async def test_cleanup_and_inactive_completion_share_one_commit_boundary(
    deactivation_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seeded = await _seed_operation(deactivation_session_factory)
    ctx, _, _ = _ctx(deactivation_session_factory)

    async def fail_if_called(**_kwargs):
        raise AssertionError("normal cleanup must already complete the operation")

    monkeypatch.setattr(account_deactivation_module, "_complete", fail_if_called)

    await deliver_account_deactivation(ctx, _event(seeded.operation_id))

    async with deactivation_session_factory() as session:
        operation = await session.get(AccountDeactivationOperation, seeded.operation_id)
        user = await session.get(User, seeded.user_id)
        assert operation is not None
        assert user is not None
        assert operation.activation_reset_at is not None
        assert operation.completed_at is not None
        assert operation.status == "completed"
        assert user.status == "inactive"


@pytest.mark.anyio
async def test_observability_snapshot_aggregates_only_safe_operation_dimensions(
    deactivation_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with deactivation_session_factory() as session:
        owner = User(
            clerk_user_id="snapshot-owner",
            email="snapshot-owner@example.com",
            status="deactivating",
        )
        ended = User(
            clerk_user_id="snapshot-ended",
            email="snapshot-ended@example.com",
            status="deactivating",
        )
        session.add_all([owner, ended])
        await session.flush()
        session.add_all(
            [
                AccountDeactivationOperation(
                    user_id=owner.id,
                    lifecycle_generation=1,
                    trigger="owner_request",
                    status="processing",
                    requested_at=NOW - timedelta(minutes=10),
                ),
                AccountDeactivationOperation(
                    user_id=ended.id,
                    lifecycle_generation=1,
                    trigger="subscription_ended",
                    status="attention_required",
                    requested_at=NOW - timedelta(minutes=2),
                    last_error_code="provider_contract",
                ),
            ]
        )
        await session.commit()

    async with deactivation_session_factory() as session:
        snapshot = await AccountDeactivationRepository(session).observability_snapshot(
            NOW
        )

    assert snapshot.counts == {
        ("owner_request", "pending"): 0,
        ("owner_request", "processing"): 1,
        ("owner_request", "attention_required"): 0,
        ("owner_request", "completed"): 0,
        ("subscription_ended", "pending"): 0,
        ("subscription_ended", "processing"): 0,
        ("subscription_ended", "attention_required"): 1,
        ("subscription_ended", "completed"): 0,
    }
    assert snapshot.oldest_incomplete_age_seconds == 600.0
    assert snapshot.attention_counts == {
        "owner_request": 0,
        "subscription_ended": 1,
    }
