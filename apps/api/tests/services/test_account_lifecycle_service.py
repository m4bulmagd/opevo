from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.account_deactivation_operation import AccountDeactivationOperation
from app.models.agent_config import AgentConfig
from app.models.outbox_event import OutboxEvent
from app.models.phone_number import PhoneNumber
from app.models.phone_number_provisioning import PhoneNumberProvisioning
from app.models.provider_cleanup_operation import ProviderCleanupOperation
from app.models.subscription import Subscription
from app.models.user import User
from app.services.account_lifecycle_service import AccountLifecycleService
from tests.fakes import FakeCustomerReadinessService


async def _seed_owner(
    database_url: str,
    *,
    external_user_id: str = "account-owner",
    status: str = "active",
    subscription_id: str | None = "sub-current",
    phone_provider_id: str | None = "pn-private",
    phone_active: bool = True,
) -> UUID:
    engine = create_async_engine(database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        user = User(
            external_user_id=external_user_id,
            email=f"{external_user_id}@example.com",
            status=status,
        )
        session.add(user)
        await session.flush()
        session.add(AgentConfig(user_id=user.id, is_enabled=True))
        if subscription_id is not None:
            now = datetime.now(UTC)
            session.add(
                Subscription(
                    user_id=user.id,
                    stripe_customer_id=f"cus-{external_user_id}",
                    stripe_subscription_id=subscription_id,
                    plan_tier="starter",
                    status="active",
                    allocated_minutes=60,
                    current_period_start=now - timedelta(days=1),
                    current_period_end=now + timedelta(days=1),
                )
            )
        if phone_provider_id is not None:
            session.add(
                PhoneNumber(
                    user_id=user.id,
                    e164=f"+331234{str(user.id.int)[-5:]}",
                    country_code="FR",
                    provider="telnyx",
                    provider_number_id=phone_provider_id,
                    provider_connection_name="app-active",
                    is_active=phone_active,
                )
            )
        await session.commit()
        user_id = user.id
    await engine.dispose()
    return user_id


async def _owner_state(
    database_url: str, user_id: UUID
) -> tuple[
    User,
    AgentConfig,
    PhoneNumber,
    list[AccountDeactivationOperation],
    list[OutboxEvent],
]:
    engine = create_async_engine(database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        user = await session.get(User, user_id)
        assert user is not None
        config = await session.scalar(
            select(AgentConfig).where(AgentConfig.user_id == user_id)
        )
        phone = await session.scalar(
            select(PhoneNumber).where(PhoneNumber.user_id == user_id)
        )
        assert config is not None
        assert phone is not None
        operations = list(
            (await session.scalars(select(AccountDeactivationOperation))).all()
        )
        events = list((await session.scalars(select(OutboxEvent))).all())
    await engine.dispose()
    return user, config, phone, operations, events


@pytest.mark.anyio
async def test_owner_deactivation_api_is_safe_idempotent_and_disables_local_service(
    async_client,
    client_database_url: str,
    rs256_clerk_token_for,
) -> None:
    user_id = await _seed_owner(client_database_url)
    headers = {"authorization": f"Bearer {rs256_clerk_token_for('account-owner')}"}

    assert (
        await async_client.post(
            "/api/account/deactivate",
            json={"confirmation": "deactivate"},
            headers=headers,
        )
    ).status_code == 422

    response = await async_client.post(
        "/api/account/deactivate",
        json={"confirmation": "DEACTIVATE"},
        headers=headers,
    )

    assert response.status_code == 202
    body = response.json()
    assert body == {
        "status": "deactivating",
        "serving": False,
        "deactivation": {
            "state": "requested",
            "requested_at": body["deactivation"]["requested_at"],
        },
        "reactivation_allowed": False,
        "blocker": "account_deactivating",
    }
    for private_field in (
        "stripe_subscription_id",
        "phone_provider_id",
        "attempt_count",
        "last_error_code",
    ):
        assert private_field not in response.text

    user, config, phone, operations, events = await _owner_state(
        client_database_url, user_id
    )
    assert user.status == "deactivating"
    assert user.lifecycle_generation == 2
    assert config.is_enabled is False
    assert phone.is_active is False
    assert len(operations) == 1
    assert len(events) == 1
    assert events[0].topic == "account.deactivate"
    assert events[0].aggregate_type == "account-deactivation-operation"
    assert events[0].aggregate_id == operations[0].id
    assert events[0].idempotency_key == f"account.deactivate:{operations[0].id}"
    assert events[0].payload.keys() == {"operation_id"}
    assert events[0].payload["operation_id"] == str(operations[0].id)

    repeated = await async_client.post(
        "/api/account/deactivate",
        json={"confirmation": "DEACTIVATE"},
        headers=headers,
    )
    assert repeated.status_code == 202
    assert repeated.json() == body
    user, _, _, operations, events = await _owner_state(client_database_url, user_id)
    assert user.lifecycle_generation == 2
    assert len(operations) == 1
    assert len(events) == 1


@pytest.mark.anyio
async def test_active_account_serving_comes_from_central_readiness(
    db_session: AsyncSession,
    active_user: User,
) -> None:
    unavailable = AccountLifecycleService(
        db_session,
        activation_flow_enabled=False,
        readiness_service=FakeCustomerReadinessService(serving=False),
    )
    available = AccountLifecycleService(
        db_session,
        activation_flow_enabled=False,
        readiness_service=FakeCustomerReadinessService(serving=True),
    )

    assert (await unavailable.get_account(active_user.id)).model_dump() == {
        "status": "active",
        "serving": False,
        "deactivation": None,
        "reactivation_allowed": False,
        "blocker": "customer_not_ready",
    }
    assert (await available.get_account(active_user.id)).model_dump() == {
        "status": "active",
        "serving": True,
        "deactivation": None,
        "reactivation_allowed": False,
        "blocker": None,
    }


@pytest.mark.anyio
async def test_inactive_response_allows_reactivation_only_after_cleanup_finishes(
    db_session: AsyncSession,
    active_user: User,
) -> None:
    active_user.status = "inactive"
    operation = AccountDeactivationOperation(
        user_id=active_user.id,
        lifecycle_generation=active_user.lifecycle_generation,
        trigger="owner_request",
        requested_at=datetime.now(UTC),
    )
    db_session.add(operation)
    await db_session.flush()
    service = AccountLifecycleService(
        db_session,
        activation_flow_enabled=False,
        readiness_service=FakeCustomerReadinessService(serving=True),
    )

    assert (await service.get_account(active_user.id)).model_dump()[
        "reactivation_allowed"
    ] is False

    operation.status = "completed"
    operation.routing_disabled_at = operation.requested_at
    operation.subscription_canceled_at = operation.requested_at
    operation.active_call_drained_at = operation.requested_at
    operation.number_released_at = operation.requested_at
    operation.activation_reset_at = operation.requested_at
    operation.completed_at = operation.requested_at
    await db_session.flush()

    phone_assignment = PhoneNumber(
        user_id=active_user.id,
        e164="+33123456789",
        country_code="FR",
        provider="telnyx",
        provider_number_id="pn-released",
        provider_connection_name="app-disabled",
        is_active=False,
    )
    db_session.add(phone_assignment)
    await db_session.flush()

    assert (await service.get_account(active_user.id)).reactivation_allowed is False

    await db_session.delete(phone_assignment)
    await db_session.flush()

    assert (await service.get_account(active_user.id)).model_dump() == {
        "status": "inactive",
        "serving": False,
        "deactivation": None,
        "reactivation_allowed": True,
        "blocker": None,
    }


@pytest.mark.anyio
async def test_inactive_cleanup_blocker_is_bounded_reactivation_not_ready(
    db_session: AsyncSession,
    active_user: User,
) -> None:
    active_user.status = "inactive"
    operation = AccountDeactivationOperation(
        user_id=active_user.id,
        lifecycle_generation=active_user.lifecycle_generation,
        trigger="owner_request",
        requested_at=datetime.now(UTC),
    )
    db_session.add(operation)
    await db_session.flush()

    response = await AccountLifecycleService(
        db_session, activation_flow_enabled=False
    ).get_account(active_user.id)

    assert response.reactivation_allowed is False
    assert response.blocker == "reactivation_not_ready"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("cleanup_status", "expected_blocker"),
    [
        ("pending", "reactivation_not_ready"),
        ("processing", "reactivation_not_ready"),
        ("attention_required", "deactivation_attention_required"),
    ],
)
async def test_inactive_response_blocks_on_unresolved_provider_cleanup(
    db_session: AsyncSession,
    active_user: User,
    cleanup_status: str,
    expected_blocker: str,
) -> None:
    active_user.status = "inactive"
    db_session.add(
        ProviderCleanupOperation(
            user_id=active_user.id,
            lifecycle_generation=active_user.lifecycle_generation,
            resource_type="stripe_subscription",
            provider_resource_id=f"sub-unresolved-{cleanup_status}",
            status=cleanup_status,
        )
    )
    await db_session.flush()

    response = await AccountLifecycleService(
        db_session, activation_flow_enabled=False
    ).get_account(active_user.id)

    assert response.reactivation_allowed is False
    assert response.blocker == expected_blocker


@pytest.mark.anyio
async def test_inactive_response_blocks_while_prior_provisioning_can_still_complete(
    db_session: AsyncSession,
    active_user: User,
) -> None:
    active_user.status = "inactive"
    db_session.add(
        PhoneNumberProvisioning(
            user_id=active_user.id,
            target_country_code="FR",
            status="running",
            attempt_count=1,
            can_retry=False,
            provider_operation_key="activation:provision:prior-generation",
        )
    )
    await db_session.flush()

    response = await AccountLifecycleService(
        db_session, activation_flow_enabled=False
    ).get_account(active_user.id)

    assert response.reactivation_allowed is False
    assert response.blocker == "reactivation_not_ready"


@pytest.mark.anyio
async def test_completed_and_stale_subscription_events_do_not_restart_deactivation(
    db_session: AsyncSession,
    active_user: User,
) -> None:
    active_user.status = "inactive"
    active_user.lifecycle_generation = 2
    completed = AccountDeactivationOperation(
        user_id=active_user.id,
        lifecycle_generation=2,
        trigger="subscription_ended",
        stripe_subscription_id="sub-completed",
        requested_at=datetime.now(UTC),
        status="completed",
    )
    completed.routing_disabled_at = completed.requested_at
    completed.subscription_canceled_at = completed.requested_at
    completed.active_call_drained_at = completed.requested_at
    completed.number_released_at = completed.requested_at
    completed.activation_reset_at = completed.requested_at
    completed.completed_at = completed.requested_at
    db_session.add(completed)
    await db_session.flush()
    service = AccountLifecycleService(db_session, activation_flow_enabled=False)

    assert (
        await service.request_in_transaction(
            active_user.id,
            trigger="subscription_ended",
            stripe_subscription_id="sub-completed",
        )
        == completed
    )

    active_user.status = "active"
    active_user.lifecycle_generation = 3
    subscription = Subscription(
        user_id=active_user.id,
        stripe_customer_id="cus-current",
        stripe_subscription_id="sub-current",
        plan_tier="starter",
        status="active",
        allocated_minutes=60,
    )
    db_session.add(subscription)
    await db_session.flush()

    assert (
        await service.request_in_transaction(
            active_user.id,
            trigger="subscription_ended",
            stripe_subscription_id="sub-completed",
        )
        is None
    )
    assert (
        await db_session.scalar(
            select(func.count()).select_from(AccountDeactivationOperation)
        )
        == 1
    )


@pytest.mark.anyio
async def test_subscription_end_request_leaves_worker_phase_timestamps_unclaimed(
    db_session: AsyncSession,
    active_user: User,
) -> None:
    subscription = Subscription(
        user_id=active_user.id,
        stripe_customer_id="cus-ended",
        stripe_subscription_id="sub-ended",
        plan_tier="starter",
        status="canceled",
        allocated_minutes=0,
    )
    db_session.add(subscription)
    await db_session.flush()
    service = AccountLifecycleService(db_session, activation_flow_enabled=False)

    operation = await service.request_in_transaction(
        active_user.id,
        trigger="subscription_ended",
        stripe_subscription_id="sub-ended",
    )

    assert operation is not None
    assert operation.routing_disabled_at is None
    assert operation.subscription_canceled_at is None
    projection = await service.get_account(active_user.id)
    assert projection.status == "deactivating"
    assert projection.deactivation is not None
    assert projection.deactivation.state == "requested"


@pytest.mark.anyio
async def test_deactivation_progress_advances_to_the_first_unfinished_phase(
    db_session: AsyncSession,
    active_user: User,
) -> None:
    active_user.status = "deactivating"
    operation = AccountDeactivationOperation(
        user_id=active_user.id,
        lifecycle_generation=active_user.lifecycle_generation,
        trigger="owner_request",
        requested_at=datetime.now(UTC),
    )
    db_session.add(operation)
    await db_session.flush()
    service = AccountLifecycleService(db_session, activation_flow_enabled=False)

    assert (await service.get_account(active_user.id)).deactivation is not None
    assert (await service.get_account(active_user.id)).deactivation.state == "requested"

    operation.status = "processing"
    await db_session.flush()
    assert (
        await service.get_account(active_user.id)
    ).deactivation.state == "disabling_routing"

    operation.routing_disabled_at = operation.requested_at
    await db_session.flush()
    assert (
        await service.get_account(active_user.id)
    ).deactivation.state == "canceling_subscription"

    operation.subscription_canceled_at = operation.requested_at
    operation.active_call_drained_at = operation.requested_at
    operation.number_released_at = operation.requested_at
    operation.activation_reset_at = operation.requested_at
    await db_session.flush()
    assert (
        await service.get_account(active_user.id)
    ).deactivation.state == "finalizing"


@pytest.mark.anyio
async def test_owner_repeat_after_completion_keeps_the_inactive_generation(
    db_session: AsyncSession,
    active_user: User,
) -> None:
    active_user.status = "inactive"
    active_user.lifecycle_generation = 2
    completed = AccountDeactivationOperation(
        user_id=active_user.id,
        lifecycle_generation=2,
        trigger="owner_request",
        requested_at=datetime.now(UTC),
        status="completed",
    )
    completed.routing_disabled_at = completed.requested_at
    completed.subscription_canceled_at = completed.requested_at
    completed.active_call_drained_at = completed.requested_at
    completed.number_released_at = completed.requested_at
    completed.activation_reset_at = completed.requested_at
    completed.completed_at = completed.requested_at
    db_session.add(completed)
    await db_session.commit()
    service = AccountLifecycleService(db_session, activation_flow_enabled=False)

    response = await service.request_owner_deactivation(
        active_user.id,
        confirmation="DEACTIVATE",
    )

    stored = await db_session.get(User, active_user.id)
    assert stored is not None
    assert stored.lifecycle_generation == 2
    assert response.status == "inactive"
    assert (
        await db_session.scalar(
            select(func.count()).select_from(AccountDeactivationOperation)
        )
        == 1
    )
