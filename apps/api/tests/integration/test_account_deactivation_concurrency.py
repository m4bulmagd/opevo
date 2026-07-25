import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base
from app.models.account_deactivation_operation import AccountDeactivationOperation
from app.models.agent_config import AgentConfig
from app.models.business_profile import BusinessProfile
from app.models.call import Call
from app.models.call_message import CallMessage
from app.models.customer_activation import CustomerActivation
from app.models.notification import Notification
from app.models.outbox_event import OutboxEvent
from app.models.phone_number import PhoneNumber
from app.models.phone_number_provisioning import PhoneNumberProvisioning
from app.models.provider_cleanup_operation import ProviderCleanupOperation
from app.models.recording_egress_operation import RecordingEgressOperation
from app.models.subscription import Subscription
from app.models.usage_ledger import UsageLedger
from app.models.user import User
from app.providers.telephony.base import TelephonyProviderError
from app.core.database import get_session
from app.repositories.agent_config_repository import AgentConfigRepository
from app.repositories.account_deactivation_repository import (
    AccountDeactivationRepository,
)
from app.repositories.call_repository import CallRepository
from app.repositories.business_profile_repository import BusinessProfileRepository
from app.repositories.notification_repository import NotificationRepository
from app.repositories.customer_activation_repository import (
    CustomerActivationRepository,
)
from app.repositories.recording_egress_operation_repository import (
    RecordingEgressOperationRepository,
)
from app.repositories.provider_cleanup_repository import ProviderCleanupRepository
from app.repositories.subscription_repository import SubscriptionRepository
from app.repositories.user_repository import UserRepository
from app.services.account_lifecycle_service import AccountLifecycleService
from app.services.account_access_policy import AccountStateBlockedError
from app.services.activation_provisioning_service import (
    ActivationProvisioningService,
)
from app.services.activation_go_live_service import ActivationGoLiveService
from app.services.billing_query_service import BillingQueryService
from app.services.billing_service import BillingService
from app.services.call_history_service import (
    CallHistoryNotFoundError,
    CallHistoryService,
)
from app.services.local_billing_service import (
    LocalBillingConflictError,
    LocalBillingService,
)
from app.services.outbox_service import OutboxService
from app.workers.jobs import account_deactivation as account_deactivation_module
from app.workers.jobs.account_deactivation import deliver_account_deactivation
from app.workers.jobs.phone_provisioning import phone_provisioning_job
from app.workers.jobs.provider_cleanup import deliver_provider_cleanup
from app.workers.jobs.outbox_delivery import OutboxDeliveryError
from app.workers.jobs.outbox_topics import (
    deliver_livekit_verification_dispatch,
    deliver_phone_provision,
    deliver_phone_routing,
)
from app.routers.calls import get_call_history_service
from app.routers.calls import router as calls_router


NOW = datetime(2026, 7, 25, 10, 0, tzinfo=UTC)
PRIVATE_CONTENT = "retained owner history"


@pytest_asyncio.fixture
async def account_session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("PostgreSQL account concurrency tests require TEST_DATABASE_URL")
    if database_url.startswith("postgresql://"):
        database_url = database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if not database_url.startswith("postgresql+asyncpg://"):
        pytest.skip("TEST_DATABASE_URL must identify PostgreSQL")

    schema_name = f"task8_account_{uuid4().hex}"
    quoted_schema = f'"{schema_name}"'
    admin = create_async_engine(database_url, isolation_level="AUTOCOMMIT")
    engine = None
    try:
        async with admin.connect() as connection:
            await connection.execute(text(f"CREATE SCHEMA {quoted_schema}"))
        engine = create_async_engine(
            database_url,
            connect_args={"server_settings": {"search_path": schema_name}},
        )
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        if engine is not None:
            await engine.dispose()
        async with admin.connect() as connection:
            await connection.execute(
                text(f"DROP SCHEMA IF EXISTS {quoted_schema} CASCADE")
            )
        await admin.dispose()


class _ForbiddenReleaseProvider:
    def __init__(self) -> None:
        self.release_calls: list[str] = []

    async def disable_number(self, *, provider_number_id: str) -> str:
        raise AssertionError("routing was already disabled")

    async def release_number(self, *, provider_number_id: str) -> None:
        self.release_calls.append(provider_number_id)
        raise AssertionError("an active call must prevent number release")


class _ForbiddenSubscriptionProvider:
    async def cancel_immediately(self, subscription_id: str) -> None:
        raise AssertionError("subscription was already canceled")


class _WorkerTelephonyProvider:
    def __init__(self, assert_provider_safe=None) -> None:
        self.calls: list[tuple[str, str]] = []
        self.assert_provider_safe = assert_provider_safe or (lambda: None)

    async def disable_number(self, *, provider_number_id: str) -> str:
        self.assert_provider_safe()
        self.calls.append(("disable", provider_number_id))
        return "app-disabled"

    async def release_number(self, *, provider_number_id: str) -> None:
        self.assert_provider_safe()
        self.calls.append(("release", provider_number_id))

    async def enable_number(self, *, provider_number_id: str) -> str:
        self.assert_provider_safe()
        self.calls.append(("enable", provider_number_id))
        return "app-active"

    async def provision_number(self, **_kwargs) -> dict:
        self.assert_provider_safe()
        self.calls.append(("provision", "unexpected"))
        return {}


class _WorkerSubscriptionProvider:
    def __init__(self, assert_provider_safe=None) -> None:
        self.calls: list[str] = []
        self.assert_provider_safe = assert_provider_safe or (lambda: None)

    async def cancel_immediately(self, subscription_id: str) -> None:
        self.assert_provider_safe()
        self.calls.append(subscription_id)


class _BarrierSubscriptionProvider(_WorkerSubscriptionProvider):
    def __init__(
        self,
        *,
        entered: asyncio.Event,
        resume: asyncio.Event,
        returned: asyncio.Event,
    ) -> None:
        super().__init__()
        self.entered = entered
        self.resume = resume
        self.returned = returned

    async def cancel_immediately(self, subscription_id: str) -> None:
        self.entered.set()
        await self.resume.wait()
        self.calls.append(subscription_id)
        self.returned.set()


class _BarrierProvisioningProvider(_WorkerTelephonyProvider):
    def __init__(self, *, entered: asyncio.Event, resume: asyncio.Event) -> None:
        super().__init__()
        self.entered = entered
        self.resume = resume
        self.provision_calls = 0

    async def provision_number(
        self,
        *,
        country_code: str,
        operation_key: str | None = None,
    ) -> dict:
        assert country_code == "FR"
        assert operation_key == "activation:phone.provision:pg-late"
        self.provision_calls += 1
        self.entered.set()
        await self.resume.wait()
        return {
            "e164": "+33123456789",
            "provider_number_id": "pn-pg-late",
            "provider_connection_name": "app-disabled",
        }


class _ForbiddenDispatchProvider:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def list_dispatches(self, *, room_name: str):
        self.calls.append(f"list:{room_name}")
        return []

    async def create_dispatch(self, **_kwargs):
        self.calls.append("create")
        raise AssertionError("stale verification must not invoke LiveKit")


class _RetainedRecordingPlayback:
    async def get_access_url(
        self,
        *,
        call_id: UUID,
        user_id: UUID,
        recording_object_key: str | None,
    ) -> str:
        assert recording_object_key == "recordings/retained-history.ogg"
        return "https://signed.example.invalid/retained-history"


class _TrackingSessionFactory:
    def __init__(self, delegate: async_sessionmaker[AsyncSession]) -> None:
        self.delegate = delegate
        self.sessions: list[AsyncSession] = []

    @asynccontextmanager
    async def __call__(self):
        async with self.delegate() as session:
            self.sessions.append(session)
            yield session

    def assert_provider_safe(self) -> None:
        assert all(not session.in_transaction() for session in self.sessions)


@dataclass(frozen=True)
class _ActiveAccount:
    user_id: UUID
    phone_id: UUID
    subscription_id: UUID


async def _seed_active_account(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    suffix: str,
) -> _ActiveAccount:
    async with session_factory() as session:
        user = User(
            clerk_user_id=f"task8-{suffix}-{uuid4().hex}",
            email=f"task8-{suffix}-{uuid4().hex}@example.invalid",
            country_code="FR",
        )
        session.add(user)
        await session.flush()
        phone = PhoneNumber(
            user_id=user.id,
            e164=f"+3399{uuid4().int % 100000000:08d}",
            country_code="FR",
            provider="telnyx",
            provider_number_id=f"pn-{suffix}-{uuid4().hex}",
            provider_connection_name="app-active",
            is_active=True,
        )
        subscription = Subscription(
            user_id=user.id,
            stripe_customer_id=f"cus-{suffix}-{uuid4().hex}",
            stripe_subscription_id=f"sub-{suffix}-{uuid4().hex}",
            plan_tier="starter",
            status="active",
            allocated_minutes=60,
            lifecycle_generation=1,
            current_period_start=NOW - timedelta(days=1),
            current_period_end=NOW + timedelta(days=29),
        )
        session.add_all(
            [
                phone,
                subscription,
                AgentConfig(
                    user_id=user.id,
                    agent_name="Léa",
                    owner_context=PRIVATE_CONTENT,
                    system_prompt=PRIVATE_CONTENT,
                    knowledge_base=PRIVATE_CONTENT,
                    is_enabled=True,
                ),
            ]
        )
        await session.commit()
        return _ActiveAccount(user.id, phone.id, subscription.id)


def _terminal_subscription_event(
    *,
    event_id: str,
    user: User,
    subscription: Subscription,
) -> dict:
    return {
        "id": event_id,
        "created": int(NOW.timestamp()),
        "type": "customer.subscription.deleted",
        "data": {
            "object": {
                "id": subscription.stripe_subscription_id,
                "created": int((NOW - timedelta(days=30)).timestamp()),
                "customer": subscription.stripe_customer_id,
                "status": "canceled",
                "metadata": {
                    "clerk_user_id": user.clerk_user_id,
                    "user_id": str(user.id),
                    "lifecycle_generation": "1",
                },
                "items": {
                    "data": [
                        {
                            "current_period_start": int(
                                (NOW - timedelta(days=1)).timestamp()
                            ),
                            "current_period_end": int(NOW.timestamp()),
                            "price": {"lookup_key": "starter"},
                        }
                    ]
                },
            }
        },
    }


@pytest.mark.anyio
async def test_two_owner_requests_converge_on_one_generation_operation_and_intent(
    account_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    seeded = await _seed_active_account(
        account_session_factory,
        suffix="owner-race",
    )
    barrier = asyncio.Barrier(2)

    async def request() -> UUID:
        async with account_session_factory() as session:
            await barrier.wait()
            operation = await AccountLifecycleService(session).request_in_transaction(
                seeded.user_id,
                trigger="owner_request",
            )
            assert operation is not None
            await session.commit()
            return operation.id

    operation_ids = await asyncio.gather(request(), request())

    assert operation_ids[0] == operation_ids[1]
    async with account_session_factory() as session:
        user = await session.get(User, seeded.user_id)
        operations = list(
            (
                await session.scalars(
                    select(AccountDeactivationOperation).where(
                        AccountDeactivationOperation.user_id == seeded.user_id
                    )
                )
            ).all()
        )
        events = list(
            (
                await session.scalars(
                    select(OutboxEvent).where(OutboxEvent.topic == "account.deactivate")
                )
            ).all()
        )

    assert user is not None
    assert user.status == "deactivating"
    assert user.lifecycle_generation == 2
    assert [operation.id for operation in operations] == [operation_ids[0]]
    assert operations[0].lifecycle_generation == 2
    assert len(events) == 1
    assert events[0].aggregate_id == operation_ids[0]
    assert events[0].payload == {"operation_id": str(operation_ids[0])}


@pytest.mark.anyio
async def test_late_provider_acquisition_after_deactivation_is_durably_released_once(
    account_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with account_session_factory() as session:
        user = User(
            clerk_user_id=f"late-provision-{uuid4().hex}",
            email=f"late-provision-{uuid4().hex}@example.invalid",
            country_code="FR",
        )
        session.add(user)
        await session.flush()
        session.add_all(
            [
                AgentConfig(user_id=user.id, is_enabled=True),
                Subscription(
                    user_id=user.id,
                    stripe_customer_id=f"cus-{uuid4().hex}",
                    stripe_subscription_id=f"sub-{uuid4().hex}",
                    plan_tier="starter",
                    status="active",
                    allocated_minutes=60,
                    lifecycle_generation=1,
                ),
            ]
        )
        await session.commit()
        user_id = user.id

    entered = asyncio.Event()
    resume = asyncio.Event()
    provider = _BarrierProvisioningProvider(entered=entered, resume=resume)
    provisioning_task = asyncio.create_task(
        phone_provisioning_job(
            {
                "session_factory": account_session_factory,
                "telephony_provider": provider,
            },
            {"user_id": str(user_id), "lifecycle_generation": 1},
            provider_operation_key="activation:phone.provision:pg-late",
        )
    )
    await asyncio.wait_for(entered.wait(), timeout=5)

    async with account_session_factory() as session:
        operation = await AccountLifecycleService(session).request_in_transaction(
            user_id,
            trigger="owner_request",
        )
        assert operation is not None
        await session.commit()
        event = await session.scalar(
            select(OutboxEvent).where(
                OutboxEvent.topic == "account.deactivate",
                OutboxEvent.aggregate_id == operation.id,
            )
        )
        assert event is not None
    await deliver_account_deactivation(
        {
            "session_factory": account_session_factory,
            "telephony_provider": provider,
            "subscription_provider": _WorkerSubscriptionProvider(),
            "account_deactivation_now": lambda: NOW,
        },
        event,
    )

    async with account_session_factory() as session:
        projection = await AccountLifecycleService(session).get_account(user_id)
        await session.rollback()
        checkout = await BillingQueryService(session).prepare_checkout_attempt(user_id)
        with pytest.raises(LocalBillingConflictError) as local_billing:
            await LocalBillingService(session).activate_starter(user_id, NOW)
        running = await session.scalar(
            select(PhoneNumberProvisioning).where(
                PhoneNumberProvisioning.user_id == user_id
            )
        )
        cleanup_count = await session.scalar(
            select(func.count())
            .select_from(ProviderCleanupOperation)
            .where(ProviderCleanupOperation.user_id == user_id)
        )

    assert projection.reactivation_allowed is False
    assert projection.blocker == "reactivation_not_ready"
    assert checkout.allowed is False
    assert local_billing.value.code == "local_subscription_unavailable"
    assert running is not None
    assert running.status == "running"
    assert cleanup_count == 0

    resume.set()
    with pytest.raises(AccountStateBlockedError):
        await provisioning_task

    async with account_session_factory() as session:
        user = await session.get(User, user_id)
        phone_count = await session.scalar(
            select(func.count())
            .select_from(PhoneNumber)
            .where(PhoneNumber.user_id == user_id)
        )
        cleanup = await session.scalar(
            select(ProviderCleanupOperation).where(
                ProviderCleanupOperation.user_id == user_id
            )
        )
        assert user is not None
        assert user.status == "inactive"
        assert phone_count == 0
        assert cleanup is not None
        cleanup_event = await session.scalar(
            select(OutboxEvent).where(
                OutboxEvent.topic == "provider.cleanup",
                OutboxEvent.aggregate_id == cleanup.id,
            )
        )
        assert cleanup_event is not None

    async with account_session_factory() as session:
        pending_projection = await AccountLifecycleService(session).get_account(user_id)
    assert pending_projection.reactivation_allowed is False
    assert pending_projection.blocker == "reactivation_not_ready"

    cleanup_release_entered = asyncio.Event()
    resume_cleanup_release = asyncio.Event()

    class BarrierRetryCleanupProvider:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []
            self.fail_release = True

        async def disable_number(self, *, provider_number_id: str) -> str:
            self.calls.append(("disable", provider_number_id))
            return "app-disabled"

        async def release_number(self, *, provider_number_id: str) -> None:
            self.calls.append(("release", provider_number_id))
            if self.fail_release:
                cleanup_release_entered.set()
                await resume_cleanup_release.wait()
                raise TelephonyProviderError(
                    "provider_retryable",
                    error_class="timeout",
                )

    cleanup_provider = BarrierRetryCleanupProvider()
    cleanup_ctx = {
        "session_factory": account_session_factory,
        "telephony_provider": cleanup_provider,
        "provider_cleanup_now": lambda: NOW,
    }
    first_cleanup = asyncio.create_task(
        deliver_provider_cleanup(cleanup_ctx, cleanup_event)
    )
    await asyncio.wait_for(cleanup_release_entered.wait(), timeout=5)
    async with account_session_factory() as session:
        processing = await session.get(ProviderCleanupOperation, cleanup.id)
        processing_projection = await AccountLifecycleService(session).get_account(
            user_id
        )
    assert processing is not None
    assert processing.status == "processing"
    assert processing_projection.reactivation_allowed is False
    resume_cleanup_release.set()
    with pytest.raises(OutboxDeliveryError) as retry:
        await first_cleanup
    assert retry.value.error_code == "provider_retryable"

    async with account_session_factory() as session:
        retrying = await session.get(ProviderCleanupOperation, cleanup.id)
        provisioning = await session.scalar(
            select(PhoneNumberProvisioning).where(
                PhoneNumberProvisioning.user_id == user_id
            )
        )
        retry_projection = await AccountLifecycleService(session).get_account(user_id)
    assert retrying is not None
    assert retrying.status == "pending"
    assert provisioning is not None
    assert provisioning.status == "running"
    assert retry_projection.reactivation_allowed is False

    cleanup_provider.fail_release = False
    await deliver_provider_cleanup(cleanup_ctx, cleanup_event)
    await deliver_provider_cleanup(cleanup_ctx, cleanup_event)

    async with account_session_factory() as session:
        phone_cleanup_projection = await AccountLifecycleService(session).get_account(
            user_id
        )
        provisioning_count = await session.scalar(
            select(func.count())
            .select_from(PhoneNumberProvisioning)
            .where(PhoneNumberProvisioning.user_id == user_id)
        )
        stale_cleanup = await ProviderCleanupRepository(session).adopt(
            user_id=user_id,
            lifecycle_generation=2,
            resource_type="stripe_subscription",
            provider_resource_id="sub-stale-after-deactivation",
        )
        stale_event = await OutboxService(session).add(
            topic="provider.cleanup",
            aggregate_type="provider-cleanup-operation",
            aggregate_id=stale_cleanup.id,
            idempotency_key=f"provider.cleanup:{stale_cleanup.id}",
            payload={"cleanup_operation_id": str(stale_cleanup.id)},
        )
        await session.commit()
    assert phone_cleanup_projection.reactivation_allowed is True
    assert provisioning_count == 0

    async with account_session_factory() as session:
        stale_projection = await AccountLifecycleService(session).get_account(user_id)
        stale_checkout = await BillingQueryService(session).prepare_checkout_attempt(
            user_id
        )
    assert stale_projection.reactivation_allowed is False
    assert stale_projection.blocker == "reactivation_not_ready"
    assert stale_checkout.allowed is False

    subscriptions = _WorkerSubscriptionProvider()
    await deliver_provider_cleanup(
        {
            "session_factory": account_session_factory,
            "subscription_provider": subscriptions,
            "provider_cleanup_now": lambda: NOW,
        },
        stale_event,
    )
    await deliver_provider_cleanup(
        {
            "session_factory": account_session_factory,
            "subscription_provider": subscriptions,
            "provider_cleanup_now": lambda: NOW,
        },
        stale_event,
    )

    assert provider.provision_calls == 1
    assert cleanup_provider.calls == [
        ("disable", "pn-pg-late"),
        ("release", "pn-pg-late"),
        ("release", "pn-pg-late"),
    ]
    assert subscriptions.calls == ["sub-stale-after-deactivation"]
    async with account_session_factory() as session:
        completed_projection = await AccountLifecycleService(session).get_account(
            user_id
        )
        completed_checkout = await BillingQueryService(
            session
        ).get_checkout_eligibility(user_id)
    assert completed_projection.reactivation_allowed is True
    assert completed_checkout.allowed is True


@pytest.mark.anyio
async def test_reclaimed_provider_cleanup_is_single_flight_without_transactions(
    account_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with account_session_factory() as session:
        user = User(
            clerk_user_id=f"cleanup-single-flight-{uuid4().hex}",
            email=f"cleanup-single-flight-{uuid4().hex}@example.invalid",
        )
        session.add(user)
        await session.flush()
        cleanup = ProviderCleanupOperation(
            user_id=user.id,
            lifecycle_generation=user.lifecycle_generation,
            resource_type="stripe_subscription",
            provider_resource_id="sub-cleanup-single-flight",
            status="pending",
        )
        session.add(cleanup)
        await session.flush()
        event = await OutboxService(session).add(
            topic="provider.cleanup",
            aggregate_type="provider-cleanup-operation",
            aggregate_id=cleanup.id,
            idempotency_key=f"provider.cleanup:{cleanup.id}",
            payload={"cleanup_operation_id": str(cleanup.id)},
        )
        await session.commit()
        cleanup_id = cleanup.id

    tracking_factory = _TrackingSessionFactory(account_session_factory)
    entered = asyncio.Event()
    resume = asyncio.Event()
    second_provider_call = asyncio.Event()
    calls: list[str] = []

    class BarrierSubscriptionProvider:
        async def cancel_immediately(self, subscription_id: str) -> None:
            tracking_factory.assert_provider_safe()
            calls.append(subscription_id)
            if len(calls) == 1:
                entered.set()
                await resume.wait()
            else:
                second_provider_call.set()

    ctx = {
        "session_factory": tracking_factory,
        "subscription_provider": BarrierSubscriptionProvider(),
        "provider_cleanup_now": lambda: NOW,
    }
    first = asyncio.create_task(deliver_provider_cleanup(ctx, event))
    await asyncio.wait_for(entered.wait(), timeout=5)
    reclaimed = asyncio.create_task(deliver_provider_cleanup(ctx, event))
    await asyncio.sleep(0.1)
    overlapped = second_provider_call.is_set()
    resume.set()
    results = await asyncio.gather(first, reclaimed, return_exceptions=True)

    assert overlapped is False
    assert results == [None, None]
    assert calls == ["sub-cleanup-single-flight"]
    async with account_session_factory() as session:
        stored = await session.get(ProviderCleanupOperation, cleanup_id)
        assert stored is not None
        assert stored.status == "completed"
        assert stored.attempt_count == 1


@pytest.mark.anyio
@pytest.mark.parametrize("first_commit", ["owner", "stripe"])
async def test_owner_request_and_terminal_stripe_event_converge_on_one_operation(
    account_session_factory: async_sessionmaker[AsyncSession],
    first_commit: str,
) -> None:
    seeded = await _seed_active_account(
        account_session_factory,
        suffix="stripe-race",
    )
    async with account_session_factory() as session:
        user = await session.get(User, seeded.user_id)
        subscription = await session.get(Subscription, seeded.subscription_id)
        assert user is not None
        assert subscription is not None
        envelope = _terminal_subscription_event(
            event_id=f"evt-terminal-race-{uuid4().hex}",
            user=user,
            subscription=subscription,
        )
    competing_started = asyncio.Event()

    async def owner_request() -> None:
        async with account_session_factory() as session:
            competing_started.set()
            operation = await AccountLifecycleService(session).request_in_transaction(
                seeded.user_id,
                trigger="owner_request",
            )
            assert operation is not None
            await session.commit()

    async def stripe_event() -> None:
        async with account_session_factory() as session:
            competing_started.set()
            assert await BillingService(session).handle_event(envelope) is True

    async with account_session_factory() as first_session:
        locked_user = await UserRepository(first_session).get_by_id_for_update(
            seeded.user_id
        )
        assert locked_user is not None
        competing = asyncio.create_task(
            stripe_event() if first_commit == "owner" else owner_request()
        )
        await asyncio.wait_for(competing_started.wait(), timeout=1)
        if first_commit == "owner":
            operation = await AccountLifecycleService(
                first_session
            ).request_in_transaction(
                seeded.user_id,
                trigger="owner_request",
            )
            assert operation is not None
            await first_session.commit()
        else:
            assert await BillingService(first_session).handle_event(envelope) is True
        await asyncio.wait_for(competing, timeout=2)

    async with account_session_factory() as session:
        user = await session.get(User, seeded.user_id)
        subscription = await session.get(Subscription, seeded.subscription_id)
        operations = list(
            (
                await session.scalars(
                    select(AccountDeactivationOperation).where(
                        AccountDeactivationOperation.user_id == seeded.user_id
                    )
                )
            ).all()
        )
        events = list(
            (
                await session.scalars(
                    select(OutboxEvent).where(OutboxEvent.topic == "account.deactivate")
                )
            ).all()
        )

    assert user is not None
    assert subscription is not None
    assert user.status == "deactivating"
    assert user.lifecycle_generation == 2
    assert subscription.status == "canceled"
    assert len(operations) == 1
    assert operations[0].trigger == (
        "owner_request" if first_commit == "owner" else "subscription_ended"
    )
    assert len(events) == 1
    assert events[0].aggregate_id == operations[0].id
    assert events[0].payload == {"operation_id": str(operations[0].id)}


@pytest.mark.anyio
async def test_worker_cancellation_commit_and_lifecycle_entry_use_compatible_locks(
    account_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seeded = await _seed_active_account(
        account_session_factory,
        suffix="worker-lifecycle-locks",
    )
    async with account_session_factory() as session:
        operation = await AccountLifecycleService(session).request_in_transaction(
            seeded.user_id,
            trigger="owner_request",
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

    provider_entered = asyncio.Event()
    resume_provider = asyncio.Event()
    provider_returned = asyncio.Event()
    lifecycle_subscription_locked = asyncio.Event()
    resume_lifecycle = asyncio.Event()
    worker_post_provider_phase = asyncio.Event()
    original_operation_lock = AccountDeactivationRepository.get_by_id_for_update
    original_subscription_lock = SubscriptionRepository.get_by_user_id_for_update

    async def instrument_operation_lock(repository, target_operation_id):
        result = await original_operation_lock(repository, target_operation_id)
        task = asyncio.current_task()
        if (
            task is not None
            and task.get_name() == "task8-cancellation-worker"
            and provider_returned.is_set()
        ):
            worker_post_provider_phase.set()
        return result

    async def instrument_subscription_lock(repository, user_id):
        task = asyncio.current_task()
        if (
            task is not None
            and task.get_name() == "task8-cancellation-worker"
            and provider_returned.is_set()
        ):
            worker_post_provider_phase.set()
        result = await original_subscription_lock(repository, user_id)
        if task is not None and task.get_name() == "task8-lifecycle-entry":
            lifecycle_subscription_locked.set()
            await resume_lifecycle.wait()
        return result

    monkeypatch.setattr(
        AccountDeactivationRepository,
        "get_by_id_for_update",
        instrument_operation_lock,
    )
    monkeypatch.setattr(
        SubscriptionRepository,
        "get_by_user_id_for_update",
        instrument_subscription_lock,
    )
    subscription_provider = _BarrierSubscriptionProvider(
        entered=provider_entered,
        resume=resume_provider,
        returned=provider_returned,
    )
    telephony = _WorkerTelephonyProvider()

    async def deliver() -> None:
        await deliver_account_deactivation(
            {
                "session_factory": account_session_factory,
                "telephony_provider": telephony,
                "subscription_provider": subscription_provider,
                "account_deactivation_now": lambda: NOW,
            },
            event,
        )

    async def enter_lifecycle() -> UUID:
        async with account_session_factory() as session:
            existing = await AccountLifecycleService(session).request_in_transaction(
                seeded.user_id,
                trigger="owner_request",
            )
            assert existing is not None
            await session.commit()
            return existing.id

    worker = asyncio.create_task(deliver(), name="task8-cancellation-worker")
    await asyncio.wait_for(provider_entered.wait(), timeout=2)
    lifecycle = asyncio.create_task(
        enter_lifecycle(),
        name="task8-lifecycle-entry",
    )
    await asyncio.wait_for(lifecycle_subscription_locked.wait(), timeout=2)
    resume_provider.set()
    await asyncio.wait_for(worker_post_provider_phase.wait(), timeout=2)
    resume_lifecycle.set()
    results = await asyncio.wait_for(
        asyncio.gather(worker, lifecycle, return_exceptions=True),
        timeout=5,
    )

    assert results == [None, operation_id]
    assert subscription_provider.calls
    async with account_session_factory() as session:
        operation = await session.get(AccountDeactivationOperation, operation_id)
        assert operation is not None
        assert operation.status == "completed"


async def _seed_drained_operation_with_call(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    call_status: str,
) -> tuple[UUID, UUID]:
    async with session_factory() as session:
        user = User(
            clerk_user_id=f"drain-{call_status}-{uuid4().hex}",
            email=f"drain-{call_status}-{uuid4().hex}@example.invalid",
            status="deactivating",
            lifecycle_generation=2,
        )
        session.add(user)
        await session.flush()
        phone = PhoneNumber(
            user_id=user.id,
            e164="+33999000001",
            country_code="FR",
            provider="telnyx",
            provider_number_id=f"pn-drain-{call_status}",
            provider_connection_name="app-disabled",
            is_active=False,
        )
        session.add_all(
            [
                phone,
                Subscription(
                    user_id=user.id,
                    stripe_customer_id=f"cus-drain-{call_status}-{uuid4().hex}",
                    stripe_subscription_id=f"sub-drain-{call_status}-{uuid4().hex}",
                    plan_tier="starter",
                    status="canceled",
                    allocated_minutes=0,
                    lifecycle_generation=1,
                ),
            ]
        )
        await session.flush()
        call = Call(
            user_id=user.id,
            phone_number_id=phone.id,
            livekit_room_id=f"room-drain-{call_status}-{uuid4().hex}",
            status=call_status,
        )
        operation = AccountDeactivationOperation(
            user_id=user.id,
            lifecycle_generation=2,
            trigger="owner_request",
            status="processing",
            stripe_subscription_id=None,
            phone_provider_id=phone.provider_number_id,
            requested_at=NOW - timedelta(minutes=5),
            routing_disabled_at=NOW - timedelta(minutes=4),
            subscription_canceled_at=NOW - timedelta(minutes=3),
            active_call_drained_at=NOW - timedelta(minutes=2),
        )
        session.add_all([call, operation])
        await session.flush()
        event = OutboxEvent(
            topic="account.deactivate",
            aggregate_type="account-deactivation-operation",
            aggregate_id=operation.id,
            idempotency_key=f"account.deactivate:{operation.id}",
            payload={"operation_id": str(operation.id)},
            next_attempt_at=NOW,
        )
        session.add(event)
        await session.commit()
        return operation.id, call.id


@pytest.mark.anyio
@pytest.mark.parametrize(
    "call_status",
    ["pending", "connected", "ending", "finalizing"],
)
async def test_stale_drain_evidence_never_releases_while_call_is_active(
    account_session_factory: async_sessionmaker[AsyncSession],
    call_status: str,
) -> None:
    operation_id, call_id = await _seed_drained_operation_with_call(
        account_session_factory,
        call_status=call_status,
    )
    async with account_session_factory() as session:
        event = await session.scalar(
            select(OutboxEvent).where(OutboxEvent.aggregate_id == operation_id)
        )
        assert event is not None
        assert event.payload == {"operation_id": str(operation_id)}

    telephony = _ForbiddenReleaseProvider()
    with pytest.raises(OutboxDeliveryError) as error:
        await deliver_account_deactivation(
            {
                "session_factory": account_session_factory,
                "telephony_provider": telephony,
                "subscription_provider": _ForbiddenSubscriptionProvider(),
                "account_deactivation_now": lambda: NOW,
            },
            event,
        )

    assert error.value.error_code == "account_call_draining"
    assert error.value.retryable is True
    assert telephony.release_calls == []
    async with account_session_factory() as session:
        operation = await session.get(AccountDeactivationOperation, operation_id)
        call = await session.get(Call, call_id)
        assert operation is not None
        assert call is not None
        assert call.status == call_status
        assert operation.number_released_at is None
        assert operation.activation_reset_at is None
        assert operation.completed_at is None


@pytest.mark.anyio
@pytest.mark.parametrize(
    "call_status",
    ["pending", "connected", "ending", "finalizing"],
)
async def test_pre_reset_recheck_blocks_every_active_call_after_number_release(
    account_session_factory: async_sessionmaker[AsyncSession],
    call_status: str,
) -> None:
    operation_id, call_id = await _seed_drained_operation_with_call(
        account_session_factory,
        call_status=call_status,
    )
    async with account_session_factory() as session:
        operation = await session.get(AccountDeactivationOperation, operation_id)
        assert operation is not None
        operation.number_released_at = NOW - timedelta(minutes=1)
        await session.commit()
        snapshot = account_deactivation_module._snapshot(operation)

    with pytest.raises(OutboxDeliveryError) as error:
        await account_deactivation_module._reset_activation(
            session_factory=account_session_factory,
            operation=snapshot,
            now_provider=lambda: NOW,
            telemetry=object(),
        )

    assert error.value.error_code == "account_call_draining"
    async with account_session_factory() as session:
        operation = await session.get(AccountDeactivationOperation, operation_id)
        call = await session.get(Call, call_id)
        assert operation is not None
        assert call is not None
        assert operation.number_released_at is not None
        assert operation.activation_reset_at is None
        assert operation.completed_at is None
        assert call.status == call_status
        assert call.phone_number_id is not None


@pytest.mark.anyio
async def test_call_history_detachment_holds_no_user_phone_or_operation_locks(
    account_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operation_id, call_id = await _seed_drained_operation_with_call(
        account_session_factory,
        call_status="completed",
    )
    async with account_session_factory() as session:
        operation = await session.get(AccountDeactivationOperation, operation_id)
        assert operation is not None
        operation.number_released_at = NOW - timedelta(minutes=1)
        await session.commit()
        snapshot = account_deactivation_module._snapshot(operation)

    original_detach = CallRepository.detach_phone_number
    observed_locks: list[tuple[str, str]] = []

    async def assert_standalone_detach(repository, phone_number_id):
        rows = await repository.session.execute(
            text(
                """
                SELECT relation.relname, lock.mode
                FROM pg_locks AS lock
                JOIN pg_class AS relation ON relation.oid = lock.relation
                WHERE lock.pid = pg_backend_pid()
                  AND lock.granted
                  AND relation.relname IN (
                    'users',
                    'subscriptions',
                    'phone_numbers',
                    'account_deactivation_operations'
                  )
                  AND lock.mode <> 'AccessShareLock'
                ORDER BY relation.relname, lock.mode
                """
            )
        )
        observed_locks.extend((str(name), str(mode)) for name, mode in rows)
        assert observed_locks == []
        return await original_detach(repository, phone_number_id)

    monkeypatch.setattr(
        CallRepository,
        "detach_phone_number",
        assert_standalone_detach,
    )
    completed_at = await account_deactivation_module._reset_activation(
        session_factory=account_session_factory,
        operation=snapshot,
        now_provider=lambda: NOW,
        telemetry=object(),
    )

    assert completed_at == NOW
    async with account_session_factory() as session:
        call = await session.get(Call, call_id)
        operation = await session.get(AccountDeactivationOperation, operation_id)
        assert call is not None
        assert operation is not None
        assert call.phone_number_id is None
        assert operation.completed_at == NOW


@pytest.mark.anyio
async def test_stale_enable_provision_invoice_and_go_live_work_is_provider_free(
    account_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    seeded = await _seed_active_account(
        account_session_factory,
        suffix="stale-work",
    )
    async with account_session_factory() as session:
        operation = await AccountLifecycleService(session).request_in_transaction(
            seeded.user_id,
            trigger="owner_request",
        )
        assert operation is not None
        await session.commit()

    telephony = _WorkerTelephonyProvider()
    stale_events = [
        OutboxEvent(
            topic="phone.provision",
            aggregate_type="user",
            aggregate_id=seeded.user_id,
            idempotency_key=f"stale-provision-{uuid4().hex}",
            payload={
                "user_id": str(seeded.user_id),
                "lifecycle_generation": 1,
            },
            next_attempt_at=NOW,
        ),
        OutboxEvent(
            topic="phone.enable",
            aggregate_type="user",
            aggregate_id=seeded.user_id,
            idempotency_key=f"stale-go-live-{uuid4().hex}",
            payload={
                "user_id": str(seeded.user_id),
                "lifecycle_generation": 1,
            },
            next_attempt_at=NOW,
        ),
    ]
    deliveries = (deliver_phone_provision, deliver_phone_routing)
    for delivery, event in zip(deliveries, stale_events, strict=True):
        with pytest.raises(OutboxDeliveryError) as error:
            await delivery(
                {
                    "session_factory": account_session_factory,
                    "telephony_provider": telephony,
                },
                event,
            )
        assert error.value.error_code == "dispatch_ineligible"

    async with account_session_factory() as session:
        user = await session.get(User, seeded.user_id)
        subscription = await session.get(Subscription, seeded.subscription_id)
        assert user is not None
        assert subscription is not None
        invoice = {
            "id": f"evt-stale-invoice-{uuid4().hex}",
            "created": int(NOW.timestamp()),
            "type": "invoice.paid",
            "data": {
                "object": {
                    "id": f"in-stale-{uuid4().hex}",
                    "customer": subscription.stripe_customer_id,
                    "status": "paid",
                    "paid": True,
                    "parent": {
                        "subscription_details": {
                            "subscription": subscription.stripe_subscription_id,
                            "metadata": {
                                "clerk_user_id": user.clerk_user_id,
                                "user_id": str(user.id),
                                "lifecycle_generation": "1",
                                "plan_tier": "starter",
                            },
                        }
                    },
                    "lines": {"data": []},
                }
            },
        }
        assert await BillingService(session).handle_event(invoice) is True

    assert telephony.calls == []
    async with account_session_factory() as session:
        user = await session.get(User, seeded.user_id)
        subscription = await session.get(Subscription, seeded.subscription_id)
        assert user is not None
        assert subscription is not None
        assert user.status == "deactivating"
        assert user.lifecycle_generation == 2
        assert subscription.status == "active"
        assert (
            await session.scalar(
                select(func.count())
                .select_from(UsageLedger)
                .where(UsageLedger.user_id == seeded.user_id)
            )
            == 0
        )
        assert await session.get(PhoneNumber, seeded.phone_id) is not None


@pytest.mark.anyio
async def test_stale_verification_dispatch_is_provider_free_and_keeps_claim_state(
    account_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    seeded = await _seed_active_account(
        account_session_factory,
        suffix="stale-verification",
    )
    session_id = str(uuid4())
    async with account_session_factory() as session:
        activation = CustomerActivation(
            user_id=seeded.user_id,
            verification_window_started_at=NOW - timedelta(minutes=1),
            verification_window_expires_at=NOW + timedelta(minutes=9),
            verification_session_id=session_id,
            verification_claimed_at=NOW,
            verification_status="claimed",
        )
        session.add(activation)
        await session.flush()
        event = OutboxEvent(
            topic="livekit.verification_dispatch",
            aggregate_type="forwarding-verification",
            aggregate_id=activation.id,
            idempotency_key=f"livekit.verification_dispatch:{session_id}",
            payload={
                "activation_id": str(activation.id),
                "session_id": session_id,
                "room_name": f"verification-{session_id}",
                "lifecycle_generation": 1,
            },
            next_attempt_at=NOW,
        )
        session.add(event)
        await session.commit()
        activation_id = activation.id

    async with account_session_factory() as session:
        operation = await AccountLifecycleService(session).request_in_transaction(
            seeded.user_id,
            trigger="owner_request",
        )
        assert operation is not None
        await session.commit()

    provider = _ForbiddenDispatchProvider()
    with pytest.raises(OutboxDeliveryError) as error:
        await deliver_livekit_verification_dispatch(
            {
                "session_factory": account_session_factory,
                "livekit_dispatch_provider": provider,
                "verification_now": lambda: NOW,
            },
            event,
        )

    assert error.value.error_code == "dispatch_ineligible"
    assert provider.calls == []
    async with account_session_factory() as session:
        activation = await session.get(CustomerActivation, activation_id)
        user = await session.get(User, seeded.user_id)
        assert activation is not None
        assert user is not None
        assert user.status == "deactivating"
        assert activation.verification_status == "claimed"
        assert activation.verification_dispatch_id is None


@pytest.mark.anyio
async def test_next_generation_checkout_and_provisioning_wait_for_cleanup(
    account_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with account_session_factory() as session:
        user = User(
            clerk_user_id=f"generation-block-{uuid4().hex}",
            email=f"generation-block-{uuid4().hex}@example.invalid",
            status="inactive",
            lifecycle_generation=2,
            country_code="FR",
        )
        session.add(user)
        await session.flush()
        phone = PhoneNumber(
            user_id=user.id,
            e164="+33999000002",
            country_code="FR",
            provider="telnyx",
            provider_number_id=f"pn-generation-block-{uuid4().hex}",
            provider_connection_name="app-disabled",
            is_active=False,
        )
        operation = AccountDeactivationOperation(
            user_id=user.id,
            lifecycle_generation=2,
            trigger="owner_request",
            status="processing",
            requested_at=NOW - timedelta(minutes=5),
            routing_disabled_at=NOW - timedelta(minutes=4),
            subscription_canceled_at=NOW - timedelta(minutes=3),
            active_call_drained_at=NOW - timedelta(minutes=2),
        )
        session.add_all([phone, operation])
        await session.commit()
        user_id = user.id
        phone_id = phone.id
        operation_id = operation.id

    async with account_session_factory() as session:
        eligibility = await BillingQueryService(session).get_checkout_eligibility(
            user_id
        )
        await session.rollback()
        with pytest.raises(LocalBillingConflictError) as billing_error:
            await LocalBillingService(session).activate_starter(
                user_id,
                NOW,
            )
        with pytest.raises(AccountStateBlockedError) as provisioning_error:
            await ActivationProvisioningService(session).confirm(
                user_id,
                arq_pool=None,
            )

    assert eligibility.allowed is False
    assert eligibility.lifecycle_generation == 2
    assert billing_error.value.code == "local_subscription_unavailable"
    assert provisioning_error.value.code == "account_inactive"
    async with account_session_factory() as session:
        operation = await session.get(AccountDeactivationOperation, operation_id)
        user = await session.get(User, user_id)
        phone = await session.get(PhoneNumber, phone_id)
        assert operation is not None
        assert user is not None
        assert phone is not None
        assert operation.completed_at is None
        assert user.status == "inactive"
        assert user.lifecycle_generation == 2
        assert (
            await session.scalar(
                select(func.count())
                .select_from(Subscription)
                .where(Subscription.user_id == user_id)
            )
            == 0
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(PhoneNumberProvisioning)
                .where(PhoneNumberProvisioning.user_id == user_id)
            )
            == 0
        )


@pytest.mark.anyio
async def test_stale_go_live_command_preserves_activation_config_and_outbox(
    account_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    seeded = await _seed_active_account(
        account_session_factory,
        suffix="stale-go-live-command",
    )
    async with account_session_factory() as session:
        prior_enable_at = NOW - timedelta(hours=1)
        activation = CustomerActivation(
            user_id=seeded.user_id,
            profile_confirmed_revision=1,
            profile_confirmed_at=NOW - timedelta(hours=1),
            verification_status="succeeded",
            forwarding_verified_at=NOW - timedelta(minutes=5),
            go_live_requested_at=prior_enable_at,
            go_live_approved_at=prior_enable_at,
            activated_at=prior_enable_at + timedelta(minutes=1),
        )
        session.add(activation)
        await session.flush()
        activation_id = activation.id
        prior_enable_event_id = uuid4()
        prior_enable_key = (
            f"activation:go-live:{activation_id}:attempt:1784970000000000"
        )
        prior_enable_payload = {
            "user_id": str(seeded.user_id),
            "lifecycle_generation": 1,
        }
        session.add(
            OutboxEvent(
                id=prior_enable_event_id,
                topic="phone.enable",
                aggregate_type="user",
                aggregate_id=seeded.user_id,
                idempotency_key=prior_enable_key,
                payload=prior_enable_payload,
                status="delivered",
                next_attempt_at=prior_enable_at,
                delivered_at=prior_enable_at,
            )
        )
        await session.commit()

    async with account_session_factory() as session:
        operation = await AccountLifecycleService(session).request_in_transaction(
            seeded.user_id,
            trigger="owner_request",
        )
        assert operation is not None
        await session.commit()

    async with account_session_factory() as session:
        activation = await session.get(CustomerActivation, activation_id)
        config = await session.scalar(
            select(AgentConfig).where(AgentConfig.user_id == seeded.user_id)
        )
        before_enable_events = list(
            (
                await session.scalars(
                    select(OutboxEvent)
                    .where(
                        OutboxEvent.topic == "phone.enable",
                        OutboxEvent.aggregate_type == "user",
                        OutboxEvent.aggregate_id == seeded.user_id,
                    )
                    .order_by(OutboxEvent.id)
                )
            ).all()
        )
        assert activation is not None
        assert config is not None
        before_activation = (
            activation.go_live_requested_at,
            activation.go_live_approved_at,
            activation.activated_at,
            activation.last_failure_code,
        )
        before_config = config.is_enabled
        before_enable_evidence = [
            (
                event.id,
                event.topic,
                event.aggregate_type,
                event.aggregate_id,
                event.idempotency_key,
                dict(event.payload),
                event.status,
            )
            for event in before_enable_events
        ]
        expected_enable_evidence = [
            (
                prior_enable_event_id,
                "phone.enable",
                "user",
                seeded.user_id,
                prior_enable_key,
                prior_enable_payload,
                "delivered",
            )
        ]
        assert before_enable_evidence == expected_enable_evidence

        with pytest.raises(AccountStateBlockedError) as error:
            await ActivationGoLiveService(
                session,
                now_provider=lambda: NOW,
            ).go_live(
                seeded.user_id,
                arq_pool=None,
            )

    assert error.value.code == "account_deactivating"
    async with account_session_factory() as session:
        activation = await session.get(CustomerActivation, activation_id)
        config = await session.scalar(
            select(AgentConfig).where(AgentConfig.user_id == seeded.user_id)
        )
        enable_events = list(
            (
                await session.scalars(
                    select(OutboxEvent)
                    .where(
                        OutboxEvent.topic == "phone.enable",
                        OutboxEvent.aggregate_type == "user",
                        OutboxEvent.aggregate_id == seeded.user_id,
                    )
                    .order_by(OutboxEvent.id)
                )
            ).all()
        )
        assert activation is not None
        assert config is not None
        assert (
            activation.go_live_requested_at,
            activation.go_live_approved_at,
            activation.activated_at,
            activation.last_failure_code,
        ) == before_activation
        assert config.is_enabled is before_config is False
        after_enable_evidence = [
            (
                event.id,
                event.topic,
                event.aggregate_type,
                event.aggregate_id,
                event.idempotency_key,
                dict(event.payload),
                event.status,
            )
            for event in enable_events
        ]
        assert after_enable_evidence == before_enable_evidence
        assert len(after_enable_evidence) - len(before_enable_evidence) == 0


@pytest.mark.anyio
async def test_completion_removes_only_number_projections_and_preserves_history(
    account_session_factory: async_sessionmaker[AsyncSession],
    rs256_clerk_token_for,
) -> None:
    seeded = await _seed_active_account(
        account_session_factory,
        suffix="preservation",
    )
    async with account_session_factory() as session:
        profile = BusinessProfile(
            user_id=seeded.user_id,
            owner_name="Camille",
            business_name=PRIVATE_CONTENT,
            public_description=PRIVATE_CONTENT,
            receptionist_name="Léa",
            special_instructions=PRIVATE_CONTENT,
            detected_carrier="orange",
            detected_number_type="mobile",
            carrier_lookup_status="confirmed",
            carrier_looked_up_at=NOW - timedelta(days=4),
            confirmed_carrier="orange",
            content_revision=7,
        )
        activation = CustomerActivation(
            user_id=seeded.user_id,
            profile_confirmed_revision=7,
            profile_confirmed_at=NOW - timedelta(days=3),
            provisioning_consented_at=NOW - timedelta(days=2),
            provisioning_idempotency_key=f"provision-{uuid4().hex}",
            verification_window_started_at=NOW - timedelta(hours=2),
            verification_window_expires_at=NOW + timedelta(hours=2),
            verification_session_id=str(uuid4()),
            verification_claimed_at=NOW - timedelta(hours=1),
            verification_dispatch_id=f"dispatch-{uuid4().hex}",
            verification_routing_fingerprint="a" * 64,
            verification_status="succeeded",
            verified_routing_fingerprint="b" * 64,
            forwarding_verified_at=NOW - timedelta(hours=1),
            go_live_requested_at=NOW - timedelta(minutes=30),
            go_live_approved_at=NOW - timedelta(minutes=20),
            activated_at=NOW - timedelta(minutes=10),
            last_failure_code="routing_provider_terminal",
        )
        session.add_all([profile, activation])
        await session.flush()
        call = Call(
            user_id=seeded.user_id,
            phone_number_id=seeded.phone_id,
            livekit_room_id=f"room-history-{uuid4().hex}",
            status="completed",
            caller_number="+33199000000",
            summary_text=PRIVATE_CONTENT,
            summary_data={
                "summary_text": PRIVATE_CONTENT,
                "caller_intent": "book_service",
                "action_items": ["return call"],
                "sentiment": "positive",
                "follow_up_required": True,
            },
            recording_object_key="recordings/retained-history.ogg",
            recording_egress_id="egress-retained-history",
            recording_url="https://legacy.example.invalid/retained-history",
        )
        session.add(call)
        await session.flush()
        message = CallMessage(
            call_id=call.id,
            speaker="CALLER",
            text=PRIVATE_CONTENT,
            sequence_number=1,
        )
        notification = Notification(
            user_id=seeded.user_id,
            call_id=call.id,
            notification_type="call_summary",
            status="sent",
            payload={"safe": "retained"},
        )
        usage = UsageLedger(
            user_id=seeded.user_id,
            call_id=call.id,
            event_type="call_completed",
            source_id=f"usage-{uuid4().hex}",
            minutes_delta=-1,
            balance_after=59,
        )
        recording = RecordingEgressOperation(
            call_id=call.id,
            room_name=call.livekit_room_id,
            expected_object_key=call.recording_object_key,
            provider_egress_id=call.recording_egress_id,
            start_state="started",
            start_attempted_at=NOW - timedelta(minutes=4),
            stop_requested_at=NOW - timedelta(minutes=3),
            provider_terminal_at=NOW - timedelta(minutes=2),
        )
        provisioning = PhoneNumberProvisioning(
            user_id=seeded.user_id,
            phone_number_id=seeded.phone_id,
            target_country_code="FR",
            status="succeeded",
            attempt_count=1,
            can_retry=False,
            provider_operation_key=f"provisioning-{uuid4().hex}",
        )
        summary_event = OutboxEvent(
            topic="summary.generate",
            aggregate_type="call-summary",
            aggregate_id=call.id,
            idempotency_key=f"summary.generate:{call.id}:v1",
            payload={"call_id": str(call.id)},
            next_attempt_at=NOW,
        )
        session.add_all(
            [
                message,
                notification,
                usage,
                recording,
                provisioning,
                summary_event,
            ]
        )
        await session.commit()
        seeded_agent = await AgentConfigRepository(session).get_by_user_id(
            seeded.user_id
        )
        assert seeded_agent is not None
        retained_ids = {
            "agent": seeded_agent.id,
            "profile": profile.id,
            "activation": activation.id,
            "call": call.id,
            "message": message.id,
            "notification": notification.id,
            "usage": usage.id,
            "recording": recording.id,
            "summary_event": summary_event.id,
        }
        provisioning_id = provisioning.id
        owner = await session.get(User, seeded.user_id)
        assert owner is not None
        owner_clerk_user_id = owner.clerk_user_id
        other = User(
            clerk_user_id=f"task8-preservation-other-{uuid4().hex}",
            email=f"task8-preservation-other-{uuid4().hex}@example.invalid",
            status="inactive",
        )
        session.add(other)
        await session.commit()
        other_user_id = other.id
        other_clerk_user_id = other.clerk_user_id

    async with account_session_factory() as session:
        operation = await AccountLifecycleService(session).request_in_transaction(
            seeded.user_id,
            trigger="owner_request",
        )
        assert operation is not None
        await session.commit()
        operation_id = operation.id
        event = await session.scalar(
            select(OutboxEvent).where(
                OutboxEvent.topic == "account.deactivate",
                OutboxEvent.aggregate_id == operation_id,
            )
        )
        assert event is not None
        assert event.payload == {"operation_id": str(operation_id)}

    tracking = _TrackingSessionFactory(account_session_factory)
    telephony = _WorkerTelephonyProvider(tracking.assert_provider_safe)
    subscriptions = _WorkerSubscriptionProvider(tracking.assert_provider_safe)
    await deliver_account_deactivation(
        {
            "session_factory": tracking,
            "telephony_provider": telephony,
            "subscription_provider": subscriptions,
            "account_deactivation_now": lambda: NOW,
        },
        event,
    )

    assert [name for name, _identity in telephony.calls] == ["disable", "release"]
    assert len(subscriptions.calls) == 1
    async with account_session_factory() as session:
        user = await session.get(User, seeded.user_id)
        operation = await session.get(AccountDeactivationOperation, operation_id)
        agent = await AgentConfigRepository(session).get_by_user_id(seeded.user_id)
        other_agent = await AgentConfigRepository(session).get_by_user_id(other_user_id)
        subscription = await SubscriptionRepository(session).get_by_user_id(
            seeded.user_id
        )
        other_subscription = await SubscriptionRepository(session).get_by_user_id(
            other_user_id
        )
        profile = await BusinessProfileRepository(session).get_by_user_id(
            seeded.user_id
        )
        other_profile = await BusinessProfileRepository(session).get_by_user_id(
            other_user_id
        )
        activation = await CustomerActivationRepository(session).get_by_user_id(
            seeded.user_id
        )
        call = await CallRepository(session).get_visible_by_id(
            retained_ids["call"],
            user_id=seeded.user_id,
        )
        other_call = await CallRepository(session).get_visible_by_id(
            retained_ids["call"],
            user_id=other_user_id,
        )
        messages = list(
            (
                await session.scalars(
                    select(CallMessage)
                    .join(Call, Call.id == CallMessage.call_id)
                    .where(
                        CallMessage.call_id == retained_ids["call"],
                        Call.user_id == seeded.user_id,
                        Call.deleted_at.is_(None),
                    )
                    .order_by(CallMessage.sequence_number)
                )
            ).all()
        )
        other_messages = list(
            (
                await session.scalars(
                    select(CallMessage)
                    .join(Call, Call.id == CallMessage.call_id)
                    .where(
                        CallMessage.call_id == retained_ids["call"],
                        Call.user_id == other_user_id,
                        Call.deleted_at.is_(None),
                    )
                    .order_by(CallMessage.sequence_number)
                )
            ).all()
        )
        summary_events = list(
            (
                await session.scalars(
                    select(OutboxEvent)
                    .join(Call, Call.id == OutboxEvent.aggregate_id)
                    .where(
                        OutboxEvent.topic == "summary.generate",
                        OutboxEvent.aggregate_type == "call-summary",
                        OutboxEvent.aggregate_id == retained_ids["call"],
                        Call.user_id == seeded.user_id,
                        Call.deleted_at.is_(None),
                    )
                    .order_by(OutboxEvent.id)
                )
            ).all()
        )
        other_summary_events = list(
            (
                await session.scalars(
                    select(OutboxEvent)
                    .join(Call, Call.id == OutboxEvent.aggregate_id)
                    .where(
                        OutboxEvent.topic == "summary.generate",
                        OutboxEvent.aggregate_type == "call-summary",
                        OutboxEvent.aggregate_id == retained_ids["call"],
                        Call.user_id == other_user_id,
                        Call.deleted_at.is_(None),
                    )
                    .order_by(OutboxEvent.id)
                )
            ).all()
        )
        recording = await RecordingEgressOperationRepository(
            session
        ).get_by_call_id_for_user(
            call_id=retained_ids["call"],
            user_id=seeded.user_id,
        )
        other_recording = await RecordingEgressOperationRepository(
            session
        ).get_by_call_id_for_user(
            call_id=retained_ids["call"],
            user_id=other_user_id,
        )
        notifications = await NotificationRepository(session).list_by_user_id(
            seeded.user_id
        )
        other_notifications = await NotificationRepository(session).list_by_user_id(
            other_user_id
        )
        usage = await BillingQueryService(session).get_usage_ledger(
            seeded.user_id,
            limit=20,
        )
        other_usage = await BillingQueryService(session).get_usage_ledger(
            other_user_id,
            limit=20,
        )
        history = CallHistoryService(
            session,
            recording_service=_RetainedRecordingPlayback(),
        )
        listed_calls = await history.list_calls(seeded.user_id)
        other_listed_calls = await history.list_calls(other_user_id)
        call_detail = await history.get_call_detail(
            seeded.user_id,
            retained_ids["call"],
        )
        with pytest.raises(CallHistoryNotFoundError):
            await history.get_call_detail(other_user_id, retained_ids["call"])

        assert user is not None
        assert operation is not None
        assert agent is not None
        assert subscription is not None
        assert profile is not None
        assert activation is not None
        assert call is not None
        assert recording is not None
        assert other_agent is None
        assert other_subscription is None
        assert other_profile is None
        assert other_call is None
        assert other_messages == []
        assert other_summary_events == []
        assert other_recording is None
        assert other_notifications == []
        assert other_usage.entries == []
        assert [item.id for item in listed_calls.calls] == [retained_ids["call"]]
        assert other_listed_calls.calls == []
        assert user.status == "inactive"
        assert operation.status == "completed"
        assert operation.phone_provider_id is not None
        assert agent.id == retained_ids["agent"]
        assert agent.is_enabled is False
        assert agent.owner_context == PRIVATE_CONTENT
        assert profile.id == retained_ids["profile"]
        assert profile.business_name == PRIVATE_CONTENT
        assert profile.special_instructions == PRIVATE_CONTENT
        assert (
            profile.detected_carrier,
            profile.detected_number_type,
            profile.carrier_lookup_status,
            profile.carrier_looked_up_at,
            profile.confirmed_carrier,
        ) == (
            "orange",
            "mobile",
            "confirmed",
            NOW - timedelta(days=4),
            "orange",
        )
        assert activation.id == retained_ids["activation"]
        assert activation.profile_confirmed_revision == 7
        assert activation.profile_confirmed_at is not None
        assert (
            activation.provisioning_consented_at,
            activation.provisioning_idempotency_key,
            activation.verification_window_started_at,
            activation.verification_window_expires_at,
            activation.verification_session_id,
            activation.verification_claimed_at,
            activation.verification_dispatch_id,
            activation.verification_routing_fingerprint,
            activation.verification_status,
            activation.verified_routing_fingerprint,
            activation.forwarding_verified_at,
            activation.go_live_requested_at,
            activation.go_live_approved_at,
            activation.activated_at,
            activation.last_failure_code,
        ) == (
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            "not_started",
            None,
            None,
            None,
            None,
            None,
            None,
        )
        assert call.id == retained_ids["call"]
        assert call.phone_number_id is None
        assert [
            (
                item.id,
                item.call_id,
                item.speaker,
                item.text,
                item.sequence_number,
            )
            for item in messages
        ] == [
            (
                retained_ids["message"],
                retained_ids["call"],
                "CALLER",
                PRIVATE_CONTENT,
                1,
            )
        ]
        assert [
            (
                item.id,
                item.topic,
                item.aggregate_type,
                item.aggregate_id,
                item.idempotency_key,
                dict(item.payload),
                item.status,
            )
            for item in summary_events
        ] == [
            (
                retained_ids["summary_event"],
                "summary.generate",
                "call-summary",
                retained_ids["call"],
                f"summary.generate:{retained_ids['call']}:v1",
                {"call_id": str(retained_ids["call"])},
                "pending",
            )
        ]
        assert call.summary_text == PRIVATE_CONTENT
        assert call.summary_data == {
            "summary_text": PRIVATE_CONTENT,
            "caller_intent": "book_service",
            "action_items": ["return call"],
            "sentiment": "positive",
            "follow_up_required": True,
        }
        assert call.recording_object_key == "recordings/retained-history.ogg"
        assert call.recording_egress_id == "egress-retained-history"
        assert call.recording_url == "https://legacy.example.invalid/retained-history"
        assert call_detail.id == retained_ids["call"]
        assert call_detail.summary_text == PRIVATE_CONTENT
        assert [line.text for line in call_detail.transcript] == [PRIVATE_CONTENT]
        assert (
            call_detail.recording_url
            == "https://signed.example.invalid/retained-history"
        )
        assert [notification.id for notification in notifications] == [
            retained_ids["notification"]
        ]
        assert notifications[0].payload == {"safe": "retained"}
        assert [entry.id for entry in usage.entries] == [str(retained_ids["usage"])]
        assert usage.entries[0].call_id == str(retained_ids["call"])
        assert recording.id == retained_ids["recording"]
        assert recording.call_id == call.id
        assert recording.room_name == call.livekit_room_id
        assert recording.expected_object_key == "recordings/retained-history.ogg"
        assert recording.provider_egress_id == "egress-retained-history"
        assert recording.start_state == "started"
        assert recording.start_attempted_at == NOW - timedelta(minutes=4)
        assert recording.stop_requested_at == NOW - timedelta(minutes=3)
        assert recording.provider_terminal_at == NOW - timedelta(minutes=2)
        assert subscription.id == seeded.subscription_id
        assert subscription.status == "canceled"
        assert await session.get(PhoneNumber, seeded.phone_id) is None
        assert await session.get(PhoneNumberProvisioning, provisioning_id) is None

    async def override_session():
        async with account_session_factory() as session:
            yield session

    async def override_history_service():
        async with account_session_factory() as session:
            yield CallHistoryService(
                session,
                recording_service=_RetainedRecordingPlayback(),
            )

    app = FastAPI()
    app.include_router(calls_router)
    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_call_history_service] = override_history_service
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        owner_headers = {
            "Authorization": f"Bearer {rs256_clerk_token_for(owner_clerk_user_id)}"
        }
        other_headers = {
            "Authorization": f"Bearer {rs256_clerk_token_for(other_clerk_user_id)}"
        }
        owner_list = await client.get("/api/calls", headers=owner_headers)
        owner_detail = await client.get(
            f"/api/calls/{retained_ids['call']}",
            headers=owner_headers,
        )
        other_list = await client.get("/api/calls", headers=other_headers)
        other_detail = await client.get(
            f"/api/calls/{retained_ids['call']}",
            headers=other_headers,
        )

    assert owner_list.status_code == 200
    assert [UUID(item["id"]) for item in owner_list.json()["calls"]] == [
        retained_ids["call"]
    ]
    assert owner_detail.status_code == 200
    assert UUID(owner_detail.json()["id"]) == retained_ids["call"]
    assert owner_detail.json()["transcript"][0]["text"] == PRIVATE_CONTENT
    assert other_list.status_code == 200
    assert other_list.json()["calls"] == []
    assert other_detail.status_code == 404
