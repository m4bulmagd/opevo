import json
from copy import deepcopy
from datetime import UTC, datetime
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.account_deactivation_operation import AccountDeactivationOperation
from app.models.notification import Notification
from app.models.outbox_event import OutboxEvent
from app.models.phone_number import PhoneNumber
from app.models.provider_cleanup_operation import ProviderCleanupOperation
from app.models.subscription import Subscription
from app.models.user import User
from app.models.usage_ledger import UsageLedger
from app.models.webhook_event import WebhookEvent
from app.providers.subscriptions.fake import FakeSubscriptionProvider
from app.providers.telephony.fake import FakeTelephonyProvider
from app.workers.jobs.outbox_delivery import outbox_delivery_job

from tests.fakes import MockArqPool


async def _post_stripe_event(
    async_client, signed_stripe_headers_factory, payload: dict
):
    return await async_client.post(
        "/webhooks/stripe",
        content=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        headers=signed_stripe_headers_factory(payload),
    )


async def _seed_completed_generation_one_deactivation(
    client_database_url: str,
    *,
    email: str,
) -> UUID:
    completed_at = datetime(2026, 3, 1, tzinfo=UTC)
    engine = create_async_engine(client_database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        user = User(
            clerk_user_id="user_123",
            email=email,
            status="inactive",
            lifecycle_generation=2,
        )
        session.add(user)
        await session.flush()
        session.add(
            Subscription(
                user_id=user.id,
                stripe_customer_id="cus_123",
                stripe_subscription_id="sub_generation_1",
                plan_tier="starter",
                status="canceled",
                allocated_minutes=60,
                lifecycle_generation=1,
                stripe_subscription_created_at=datetime.fromtimestamp(10, UTC),
                last_stripe_event_created_at=datetime.fromtimestamp(20, UTC),
            )
        )
        session.add(
            AccountDeactivationOperation(
                user_id=user.id,
                lifecycle_generation=2,
                trigger="owner_request",
                status="completed",
                stripe_subscription_id="sub_generation_1",
                requested_at=completed_at,
                routing_disabled_at=completed_at,
                subscription_canceled_at=completed_at,
                active_call_drained_at=completed_at,
                number_released_at=completed_at,
                activation_reset_at=completed_at,
                completed_at=completed_at,
            )
        )
        await session.commit()
        user_id = user.id
    await engine.dispose()
    return user_id


@pytest.mark.anyio
async def test_invalid_signature_is_rejected(
    async_client,
    stripe_subscription_created_payload,
) -> None:
    response = await async_client.post(
        "/webhooks/stripe",
        content=json.dumps(
            stripe_subscription_created_payload, separators=(",", ":")
        ).encode("utf-8"),
        headers={"Stripe-Signature": "t=123,v1=invalid"},
    )
    assert response.status_code == 400


@pytest.mark.anyio
async def test_unknown_event_type_returns_200_no_op(
    async_client,
    client_database_url,
    signed_stripe_headers_factory,
    stripe_subscription_created_payload,
) -> None:
    payload = {**stripe_subscription_created_payload, "type": "unknown_event_type"}
    response = await async_client.post(
        "/webhooks/stripe",
        content=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        headers=signed_stripe_headers_factory(payload),
    )
    assert response.status_code == 202

    engine = create_async_engine(client_database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        webhook_count = await session.scalar(
            select(WebhookEvent).where(WebhookEvent.external_event_id == payload["id"])
        )
        subscriptions = list((await session.execute(select(Subscription))).scalars())
        outbox_events = list((await session.execute(select(OutboxEvent))).scalars())
    await engine.dispose()

    assert webhook_count is not None
    assert subscriptions == []
    assert outbox_events == []


@pytest.mark.anyio
async def test_subscription_activation_provisions_usage_ledger(
    async_client,
    client_database_url,
    signed_stripe_headers_factory,
    stripe_subscription_created_payload,
) -> None:
    async def seed_user() -> None:
        engine = create_async_engine(client_database_url, future=True)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session:
            session.add(User(clerk_user_id="user_123", email="billing@example.com"))
            await session.commit()
        await engine.dispose()

    async def fetch_state() -> tuple[
        list[Subscription], list[UsageLedger], list[PhoneNumber]
    ]:
        engine = create_async_engine(client_database_url, future=True)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session:
            subscriptions = list(
                (await session.execute(select(Subscription))).scalars()
            )
            ledgers = list((await session.execute(select(UsageLedger))).scalars())
            phone_numbers = list((await session.execute(select(PhoneNumber))).scalars())
        await engine.dispose()
        return subscriptions, ledgers, phone_numbers

    await seed_user()

    from app.main import app

    pool = MockArqPool()
    app.state.arq_pool = pool

    response = await async_client.post(
        "/webhooks/stripe",
        content=json.dumps(
            stripe_subscription_created_payload, separators=(",", ":")
        ).encode("utf-8"),
        headers=signed_stripe_headers_factory(stripe_subscription_created_payload),
    )

    assert response.status_code == 202
    subscriptions, ledgers, phone_numbers = await fetch_state()

    assert len(pool.enqueued_jobs) == 0
    assert subscriptions[0].stripe_subscription_id == "sub_123"
    assert subscriptions[0].plan_tier == "starter"
    assert not ledgers
    assert not phone_numbers


@pytest.mark.anyio
async def test_subscription_activation_accepts_stripe_style_signature(
    async_client,
    client_database_url,
    signed_stripe_headers_factory,
    stripe_subscription_created_payload,
) -> None:
    async def seed_user() -> None:
        engine = create_async_engine(client_database_url, future=True)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session:
            session.add(User(clerk_user_id="user_123", email="billing@example.com"))
            await session.commit()
        await engine.dispose()

    await seed_user()

    response = await async_client.post(
        "/webhooks/stripe",
        content=json.dumps(
            stripe_subscription_created_payload, separators=(",", ":")
        ).encode("utf-8"),
        headers=signed_stripe_headers_factory(stripe_subscription_created_payload),
    )
    assert response.status_code == 202


@pytest.mark.anyio
async def test_subscription_activation_accepts_current_stripe_subscription_shape(
    async_client,
    client_database_url,
    signed_stripe_headers_factory,
    stripe_current_subscription_created_payload,
) -> None:
    async def seed_user() -> None:
        engine = create_async_engine(client_database_url, future=True)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session:
            session.add(User(clerk_user_id="user_123", email="billing@example.com"))
            await session.commit()
        await engine.dispose()

    await seed_user()

    response = await async_client.post(
        "/webhooks/stripe",
        content=json.dumps(
            stripe_current_subscription_created_payload, separators=(",", ":")
        ).encode("utf-8"),
        headers=signed_stripe_headers_factory(
            stripe_current_subscription_created_payload
        ),
    )
    assert response.status_code == 202


@pytest.mark.anyio
async def test_period_end_cancellation_is_only_a_serving_schedule_projection(
    async_client,
    client_database_url,
    signed_stripe_headers_factory,
    stripe_subscription_created_payload,
) -> None:
    period_end = datetime.fromtimestamp(1712592000, UTC)
    engine = create_async_engine(client_database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        user = User(clerk_user_id="user_123", email="scheduled@example.com")
        session.add(user)
        await session.flush()
        session.add(
            Subscription(
                user_id=user.id,
                stripe_customer_id="cus_123",
                stripe_subscription_id="sub_123",
                plan_tier="starter",
                status="active",
                allocated_minutes=60,
                lifecycle_generation=1,
                stripe_subscription_created_at=datetime.fromtimestamp(
                    1709990000,
                    UTC,
                ),
                last_stripe_event_created_at=datetime.fromtimestamp(
                    1710000000,
                    UTC,
                ),
            )
        )
        session.add(
            PhoneNumber(
                user_id=user.id,
                e164="+35315550170",
                country_code="IE",
                provider="telnyx",
                provider_number_id="pn_scheduled_cancel",
                provider_connection_name="app-active",
                is_active=True,
            )
        )
        await session.commit()
    await engine.dispose()

    payload = deepcopy(stripe_subscription_created_payload)
    payload.update(
        id="evt_period_end_scheduled",
        created=1710000200,
        type="customer.subscription.updated",
    )
    payload["data"]["object"]["metadata"].update(
        user_id=str(user.id),
        lifecycle_generation="1",
    )
    payload["data"]["object"]["cancel_at_period_end"] = True
    payload["data"]["object"]["cancel_at"] = int(period_end.timestamp())

    response = await _post_stripe_event(
        async_client,
        signed_stripe_headers_factory,
        payload,
    )

    assert response.status_code == 202
    engine = create_async_engine(client_database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        subscription = await session.scalar(select(Subscription))
        fetched_user = await session.scalar(select(User))
        phone = await session.scalar(select(PhoneNumber))
        operation = await session.scalar(select(AccountDeactivationOperation))
        outbox = await session.scalar(select(OutboxEvent))
    await engine.dispose()

    assert subscription is not None
    assert subscription.cancel_at_period_end is True
    assert subscription.cancellation_effective_at is not None
    assert subscription.cancellation_effective_at.replace(tzinfo=UTC) == period_end
    assert fetched_user is not None and fetched_user.status == "active"
    assert phone is not None and phone.is_active is True
    assert operation is None
    assert outbox is None


@pytest.mark.anyio
async def test_period_end_cancellation_reversal_clears_only_schedule_projection(
    async_client,
    client_database_url,
    signed_stripe_headers_factory,
    stripe_subscription_created_payload,
) -> None:
    engine = create_async_engine(client_database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        user = User(clerk_user_id="user_123", email="reversal@example.com")
        session.add(user)
        await session.flush()
        session.add(
            Subscription(
                user_id=user.id,
                stripe_customer_id="cus_123",
                stripe_subscription_id="sub_123",
                plan_tier="starter",
                status="active",
                allocated_minutes=60,
                lifecycle_generation=1,
                cancel_at_period_end=True,
                cancellation_effective_at=datetime(2026, 4, 1, tzinfo=UTC),
                stripe_subscription_created_at=datetime.fromtimestamp(
                    1709990000,
                    UTC,
                ),
                last_stripe_event_created_at=datetime.fromtimestamp(
                    1710000000,
                    UTC,
                ),
            )
        )
        await session.commit()
    await engine.dispose()

    payload = deepcopy(stripe_subscription_created_payload)
    payload.update(
        id="evt_period_end_reversed",
        created=1710000200,
        type="customer.subscription.updated",
    )
    payload["data"]["object"]["metadata"].update(
        user_id=str(user.id),
        lifecycle_generation="1",
    )
    payload["data"]["object"]["cancel_at_period_end"] = False
    payload["data"]["object"].pop("cancel_at", None)

    response = await _post_stripe_event(
        async_client,
        signed_stripe_headers_factory,
        payload,
    )

    assert response.status_code == 202
    engine = create_async_engine(client_database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        subscription = await session.scalar(select(Subscription))
        fetched_user = await session.scalar(select(User))
        operation = await session.scalar(select(AccountDeactivationOperation))
    await engine.dispose()

    assert subscription is not None
    assert subscription.cancel_at_period_end is False
    assert subscription.cancellation_effective_at is None
    assert fetched_user is not None and fetched_user.status == "active"
    assert operation is None


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("delivery_order", "first_created_at", "second_created_at"),
    [
        ("ordered", 200, 300),
        ("out-of-order", 300, 200),
    ],
)
async def test_generation_one_null_watermark_preserves_newest_schedule_projection(
    async_client,
    client_database_url,
    signed_stripe_headers_factory,
    stripe_subscription_created_payload,
    delivery_order: str,
    first_created_at: int,
    second_created_at: int,
) -> None:
    engine = create_async_engine(client_database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        user = User(
            clerk_user_id="user_123",
            email=f"legacy-schedule-{delivery_order}@example.com",
        )
        session.add(user)
        await session.flush()
        session.add(
            Subscription(
                user_id=user.id,
                stripe_customer_id="cus_123",
                stripe_subscription_id="sub_123",
                plan_tier="starter",
                status="active",
                allocated_minutes=60,
                lifecycle_generation=1,
                stripe_subscription_created_at=None,
                last_stripe_event_created_at=None,
            )
        )
        await session.commit()
    await engine.dispose()

    scheduled = deepcopy(stripe_subscription_created_payload)
    scheduled.update(
        id=f"evt_legacy_schedule_{delivery_order}",
        created=(
            first_created_at if delivery_order == "ordered" else second_created_at
        ),
        type="customer.subscription.updated",
    )
    scheduled["data"]["object"].update(
        cancel_at_period_end=True,
        cancel_at=500,
    )
    reversed_schedule = deepcopy(stripe_subscription_created_payload)
    reversed_schedule.update(
        id=f"evt_legacy_reversal_{delivery_order}",
        created=(
            second_created_at if delivery_order == "ordered" else first_created_at
        ),
        type="customer.subscription.updated",
    )
    reversed_schedule["data"]["object"].update(cancel_at_period_end=False)
    reversed_schedule["data"]["object"].pop("cancel_at", None)
    delivered = (
        (scheduled, reversed_schedule)
        if delivery_order == "ordered"
        else (reversed_schedule, scheduled)
    )

    responses = [
        await _post_stripe_event(
            async_client,
            signed_stripe_headers_factory,
            payload,
        )
        for payload in delivered
    ]

    assert [response.status_code for response in responses] == [202, 202]
    engine = create_async_engine(client_database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        subscription = await session.scalar(select(Subscription))
    await engine.dispose()

    assert subscription is not None
    assert subscription.cancel_at_period_end is False
    assert subscription.cancellation_effective_at is None
    assert subscription.last_stripe_event_created_at is not None
    assert subscription.last_stripe_event_created_at.replace(
        tzinfo=UTC
    ) == datetime.fromtimestamp(300, UTC)


@pytest.mark.anyio
async def test_current_generation_final_cancellation_starts_one_deactivation_operation(
    async_client,
    client_database_url,
    signed_stripe_headers_factory,
    stripe_subscription_created_payload,
) -> None:
    engine = create_async_engine(client_database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        user = User(clerk_user_id="user_123", email="final-cancel@example.com")
        session.add(user)
        await session.flush()
        session.add(
            Subscription(
                user_id=user.id,
                stripe_customer_id="cus_123",
                stripe_subscription_id="sub_123",
                plan_tier="starter",
                status="active",
                allocated_minutes=60,
                lifecycle_generation=1,
                stripe_subscription_created_at=datetime.fromtimestamp(
                    1709990000,
                    UTC,
                ),
                last_stripe_event_created_at=datetime.fromtimestamp(
                    1710000000,
                    UTC,
                ),
            )
        )
        session.add(
            PhoneNumber(
                user_id=user.id,
                e164="+35315550171",
                country_code="IE",
                provider="telnyx",
                provider_number_id="fake-0123456789abcdef",
                provider_connection_name="app-active",
                is_active=True,
            )
        )
        await session.commit()
    await engine.dispose()

    payload = deepcopy(stripe_subscription_created_payload)
    payload.update(
        id="evt_final_cancel",
        created=1710000200,
        type="customer.subscription.deleted",
    )
    payload["data"]["object"]["status"] = "canceled"
    payload["data"]["object"]["metadata"].update(
        user_id=str(user.id),
        lifecycle_generation="1",
    )

    first = await _post_stripe_event(
        async_client,
        signed_stripe_headers_factory,
        payload,
    )
    replay = await _post_stripe_event(
        async_client,
        signed_stripe_headers_factory,
        payload,
    )

    assert first.status_code == replay.status_code == 202
    engine = create_async_engine(client_database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        subscription = await session.scalar(select(Subscription))
        fetched_user = await session.scalar(select(User))
        phone = await session.scalar(select(PhoneNumber))
        operation = await session.scalar(select(AccountDeactivationOperation))
        outbox_events = list((await session.execute(select(OutboxEvent))).scalars())
    await engine.dispose()

    assert subscription is not None and subscription.status == "canceled"
    assert fetched_user is not None
    assert fetched_user.status == "deactivating"
    assert fetched_user.lifecycle_generation == 2
    assert phone is not None and phone.is_active is False
    assert operation is not None
    assert operation.trigger == "subscription_ended"
    assert operation.stripe_subscription_id == "sub_123"
    assert operation.routing_disabled_at is None
    assert operation.subscription_canceled_at is None
    assert len(outbox_events) == 1
    assert outbox_events[0].topic == "account.deactivate"
    assert outbox_events[0].payload == {"operation_id": str(operation.id)}

    engine = create_async_engine(client_database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    delivery = await outbox_delivery_job(
        {
            "session_factory": session_factory,
            "subscription_provider": FakeSubscriptionProvider(),
            "telephony_provider": FakeTelephonyProvider(),
        }
    )
    async with session_factory() as session:
        reconciled = await session.get(AccountDeactivationOperation, operation.id)
    await engine.dispose()

    assert delivery == {
        "claimed": 1,
        "delivered": 1,
        "retried": 0,
        "failed": 0,
    }
    assert reconciled is not None
    assert reconciled.routing_disabled_at is not None
    assert reconciled.subscription_canceled_at is not None
    assert reconciled.completed_at is not None


@pytest.mark.anyio
async def test_terminal_subscription_update_converges_without_phone_disable(
    async_client,
    client_database_url,
    signed_stripe_headers_factory,
    stripe_subscription_created_payload,
) -> None:
    engine = create_async_engine(client_database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        user = User(clerk_user_id="user_123", email="terminal-update@example.com")
        session.add(user)
        await session.flush()
        session.add(
            Subscription(
                user_id=user.id,
                stripe_customer_id="cus_123",
                stripe_subscription_id="sub_123",
                plan_tier="starter",
                status="active",
                allocated_minutes=60,
                lifecycle_generation=1,
                stripe_subscription_created_at=datetime.fromtimestamp(
                    1709990000,
                    UTC,
                ),
                last_stripe_event_created_at=datetime.fromtimestamp(
                    1710000000,
                    UTC,
                ),
            )
        )
        await session.commit()
    await engine.dispose()

    payload = deepcopy(stripe_subscription_created_payload)
    payload.update(
        id="evt_terminal_subscription_update",
        created=1710000200,
        type="customer.subscription.updated",
    )
    payload["data"]["object"]["status"] = "canceled"
    payload["data"]["object"]["metadata"].update(
        user_id=str(user.id),
        lifecycle_generation="1",
    )

    response = await _post_stripe_event(
        async_client,
        signed_stripe_headers_factory,
        payload,
    )

    assert response.status_code == 202
    engine = create_async_engine(client_database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        operation = await session.scalar(select(AccountDeactivationOperation))
        outbox_events = list((await session.execute(select(OutboxEvent))).scalars())
    await engine.dispose()

    assert operation is not None
    assert operation.trigger == "subscription_ended"
    assert len(outbox_events) == 1
    assert outbox_events[0].topic == "account.deactivate"
    assert outbox_events[0].payload == {"operation_id": str(operation.id)}


@pytest.mark.anyio
async def test_matching_generation_replacement_reactivates_without_enabling_service(
    async_client,
    client_database_url,
    signed_stripe_headers_factory,
    stripe_subscription_created_payload,
) -> None:
    user_id = await _seed_completed_generation_one_deactivation(
        client_database_url,
        email="matching-reactivation@example.com",
    )
    payload = deepcopy(stripe_subscription_created_payload)
    payload.update(id="evt_generation_2_created", created=1710000300)
    payload["data"]["object"].update(
        id="sub_generation_2",
        created=1710000200,
        status="trialing",
    )
    payload["data"]["object"]["metadata"].update(
        user_id=str(user_id),
        lifecycle_generation="2",
    )

    response = await _post_stripe_event(
        async_client,
        signed_stripe_headers_factory,
        payload,
    )

    assert response.status_code == 202
    engine = create_async_engine(client_database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        subscription = await session.scalar(select(Subscription))
        user = await session.scalar(select(User))
        phones = list((await session.execute(select(PhoneNumber))).scalars())
        outbox_events = list((await session.execute(select(OutboxEvent))).scalars())
        ledgers = list((await session.execute(select(UsageLedger))).scalars())
    await engine.dispose()

    assert subscription is not None
    assert subscription.stripe_subscription_id == "sub_generation_2"
    assert subscription.lifecycle_generation == 2
    assert subscription.status == "trialing"
    assert user is not None and user.status == "active"
    assert phones == []
    assert outbox_events == []
    assert ledgers == []


@pytest.mark.anyio
async def test_matching_replacement_can_progress_from_incomplete_to_active(
    async_client,
    client_database_url,
    signed_stripe_headers_factory,
    stripe_subscription_created_payload,
) -> None:
    user_id = await _seed_completed_generation_one_deactivation(
        client_database_url,
        email="staged-reactivation@example.com",
    )
    incomplete = deepcopy(stripe_subscription_created_payload)
    incomplete.update(id="evt_generation_2_incomplete", created=1710000300)
    incomplete["data"]["object"].update(
        id="sub_generation_2_staged",
        created=1710000200,
        status="incomplete",
    )
    incomplete["data"]["object"]["metadata"].update(
        user_id=str(user_id),
        lifecycle_generation="2",
    )
    active = deepcopy(incomplete)
    active.update(
        id="evt_generation_2_active",
        created=1710000400,
        type="customer.subscription.updated",
    )
    active["data"]["object"]["status"] = "active"

    first = await _post_stripe_event(
        async_client,
        signed_stripe_headers_factory,
        incomplete,
    )
    engine = create_async_engine(client_database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        staged_subscription = await session.scalar(select(Subscription))
        staged_user = await session.scalar(select(User))
    await engine.dispose()

    assert first.status_code == 202
    assert staged_subscription is not None
    assert staged_subscription.status == "incomplete"
    assert staged_subscription.lifecycle_generation == 2
    assert staged_user is not None and staged_user.status == "inactive"

    second = await _post_stripe_event(
        async_client,
        signed_stripe_headers_factory,
        active,
    )
    engine = create_async_engine(client_database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        current_subscription = await session.scalar(select(Subscription))
        current_user = await session.scalar(select(User))
        outbox_events = list((await session.execute(select(OutboxEvent))).scalars())
    await engine.dispose()

    assert second.status_code == 202
    assert current_subscription is not None
    assert current_subscription.status == "active"
    assert current_user is not None and current_user.status == "active"
    assert outbox_events == []


@pytest.mark.anyio
async def test_matching_generation_reactivates_inactive_account_without_old_subscription(
    async_client,
    client_database_url,
    signed_stripe_headers_factory,
    stripe_subscription_created_payload,
) -> None:
    completed_at = datetime(2026, 3, 1, tzinfo=UTC)
    engine = create_async_engine(client_database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        user = User(
            clerk_user_id="user_123",
            email="reactivation-without-old-subscription@example.com",
            status="inactive",
            lifecycle_generation=2,
        )
        session.add(user)
        await session.flush()
        session.add(
            AccountDeactivationOperation(
                user_id=user.id,
                lifecycle_generation=2,
                trigger="owner_request",
                status="completed",
                requested_at=completed_at,
                routing_disabled_at=completed_at,
                subscription_canceled_at=completed_at,
                active_call_drained_at=completed_at,
                number_released_at=completed_at,
                activation_reset_at=completed_at,
                completed_at=completed_at,
            )
        )
        await session.commit()
    await engine.dispose()

    payload = deepcopy(stripe_subscription_created_payload)
    payload.update(id="evt_generation_2_without_old_subscription", created=1710000300)
    payload["data"]["object"].update(
        id="sub_generation_2_without_old_subscription",
        created=1710000200,
        status="active",
    )
    payload["data"]["object"]["metadata"].update(
        user_id=str(user.id),
        lifecycle_generation="2",
    )

    response = await _post_stripe_event(
        async_client,
        signed_stripe_headers_factory,
        payload,
    )

    assert response.status_code == 202
    engine = create_async_engine(client_database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        subscription = await session.scalar(select(Subscription))
        reactivated_user = await session.scalar(select(User))
    await engine.dispose()

    assert subscription is not None
    assert subscription.lifecycle_generation == 2
    assert reactivated_user is not None
    assert reactivated_user.status == "active"


@pytest.mark.anyio
@pytest.mark.parametrize("invalid_generation", ["invalid", "0", "-1"])
async def test_invalid_generation_metadata_is_not_treated_as_legacy_generation_one(
    async_client,
    client_database_url,
    signed_stripe_headers_factory,
    stripe_subscription_created_payload,
    invalid_generation: str,
) -> None:
    engine = create_async_engine(client_database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        session.add(
            User(
                clerk_user_id="user_123",
                email=f"invalid-generation-{invalid_generation}@example.com",
            )
        )
        await session.commit()
    await engine.dispose()

    payload = deepcopy(stripe_subscription_created_payload)
    payload.update(id=f"evt_invalid_generation_{invalid_generation}")
    payload["data"]["object"]["metadata"]["lifecycle_generation"] = invalid_generation

    response = await _post_stripe_event(
        async_client,
        signed_stripe_headers_factory,
        payload,
    )

    assert response.status_code == 202
    engine = create_async_engine(client_database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        subscription = await session.scalar(select(Subscription))
    await engine.dispose()

    assert subscription is None


@pytest.mark.anyio
@pytest.mark.parametrize(
    "invalid_owner",
    [
        "mismatch",
        "malformed",
        "missing-explicit-generation-one",
        "missing-current",
    ],
)
async def test_subscription_rejects_invalid_internal_owner_metadata(
    async_client,
    client_database_url,
    signed_stripe_headers_factory,
    stripe_subscription_created_payload,
    invalid_owner: str,
) -> None:
    lifecycle_generation = 2 if invalid_owner == "missing-current" else 1
    engine = create_async_engine(client_database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        owner = User(
            clerk_user_id="user_123",
            email=f"subscription-owner-{invalid_owner}@example.com",
            lifecycle_generation=lifecycle_generation,
        )
        other = User(
            clerk_user_id=f"other_{invalid_owner}",
            email=f"subscription-other-{invalid_owner}@example.com",
        )
        session.add_all([owner, other])
        await session.commit()
        other_user_id = other.id
    await engine.dispose()

    payload = deepcopy(stripe_subscription_created_payload)
    payload.update(id=f"evt_subscription_invalid_owner_{invalid_owner}")
    metadata = payload["data"]["object"]["metadata"]
    metadata["lifecycle_generation"] = str(lifecycle_generation)
    if invalid_owner == "mismatch":
        metadata["user_id"] = str(other_user_id)
    elif invalid_owner == "malformed":
        metadata["user_id"] = "not-a-uuid"
    else:
        metadata.pop("user_id", None)

    response = await _post_stripe_event(
        async_client,
        signed_stripe_headers_factory,
        payload,
    )

    assert response.status_code == 202
    engine = create_async_engine(client_database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        subscriptions = list((await session.execute(select(Subscription))).scalars())
        users = list((await session.execute(select(User))).scalars())
    await engine.dispose()

    assert subscriptions == []
    assert all(user.status == "active" for user in users)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "invalid_owner",
    [
        "mismatch",
        "malformed",
        "missing-explicit-generation-one",
        "missing-current",
    ],
)
async def test_invoice_rejects_invalid_internal_owner_metadata(
    async_client,
    client_database_url,
    signed_stripe_headers_factory,
    stripe_invoice_paid_payload,
    invalid_owner: str,
) -> None:
    lifecycle_generation = 2 if invalid_owner == "missing-current" else 1
    engine = create_async_engine(client_database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        owner = User(
            clerk_user_id="user_123",
            email=f"invoice-owner-{invalid_owner}@example.com",
            lifecycle_generation=lifecycle_generation,
        )
        other = User(
            clerk_user_id=f"invoice_other_{invalid_owner}",
            email=f"invoice-other-{invalid_owner}@example.com",
        )
        session.add_all([owner, other])
        await session.flush()
        session.add(
            Subscription(
                user_id=owner.id,
                stripe_customer_id="cus_123",
                stripe_subscription_id="sub_123",
                plan_tier="starter",
                status="active",
                allocated_minutes=60,
                lifecycle_generation=lifecycle_generation,
                stripe_subscription_created_at=datetime.fromtimestamp(10, UTC),
                last_stripe_event_created_at=datetime.fromtimestamp(20, UTC),
            )
        )
        await session.commit()
        other_user_id = other.id
    await engine.dispose()

    payload = deepcopy(stripe_invoice_paid_payload)
    payload.update(id=f"evt_invoice_invalid_owner_{invalid_owner}")
    metadata = payload["data"]["object"]["parent"]["subscription_details"].setdefault(
        "metadata",
        {},
    )
    metadata.update(
        clerk_user_id="user_123",
        plan_tier="starter",
        lifecycle_generation=str(lifecycle_generation),
    )
    if invalid_owner == "mismatch":
        metadata["user_id"] = str(other_user_id)
    elif invalid_owner == "malformed":
        metadata["user_id"] = "not-a-uuid"
    else:
        metadata.pop("user_id", None)

    response = await _post_stripe_event(
        async_client,
        signed_stripe_headers_factory,
        payload,
    )

    assert response.status_code == 202
    engine = create_async_engine(client_database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        ledgers = list((await session.execute(select(UsageLedger))).scalars())
        subscription = await session.scalar(select(Subscription))
        outbox_events = list((await session.execute(select(OutboxEvent))).scalars())
    await engine.dispose()

    assert ledgers == []
    assert subscription is not None and subscription.status == "active"
    assert outbox_events == []


@pytest.mark.anyio
@pytest.mark.parametrize("metadata_generation", [None, "1"])
async def test_missing_or_old_generation_replacement_cannot_reactivate(
    async_client,
    client_database_url,
    signed_stripe_headers_factory,
    stripe_subscription_created_payload,
    metadata_generation: str | None,
) -> None:
    await _seed_completed_generation_one_deactivation(
        client_database_url,
        email=f"stale-reactivation-{metadata_generation}@example.com",
    )
    payload = deepcopy(stripe_subscription_created_payload)
    payload.update(
        id=f"evt_stale_replacement_{metadata_generation}",
        created=1710000300,
    )
    payload["data"]["object"].update(
        id=f"sub_stale_replacement_{metadata_generation}",
        created=1710000200,
        status="active",
    )
    if metadata_generation is None:
        payload["data"]["object"]["metadata"].pop(
            "lifecycle_generation",
            None,
        )
    else:
        payload["data"]["object"]["metadata"]["lifecycle_generation"] = (
            metadata_generation
        )

    response = await _post_stripe_event(
        async_client,
        signed_stripe_headers_factory,
        payload,
    )

    assert response.status_code == 202
    engine = create_async_engine(client_database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        subscription = await session.scalar(select(Subscription))
        user = await session.scalar(select(User))
        outbox_events = list((await session.execute(select(OutboxEvent))).scalars())
        cleanup = await session.scalar(select(ProviderCleanupOperation))
        ledgers = list((await session.execute(select(UsageLedger))).scalars())
    await engine.dispose()

    assert subscription is not None
    assert subscription.stripe_subscription_id == "sub_generation_1"
    assert subscription.lifecycle_generation == 1
    assert subscription.status == "canceled"
    assert user is not None and user.status == "inactive"
    assert cleanup is not None
    assert cleanup.user_id == user.id
    assert cleanup.provider_resource_id == (
        f"sub_stale_replacement_{metadata_generation}"
    )
    assert cleanup.resource_type == "stripe_subscription"
    assert len(outbox_events) == 1
    assert outbox_events[0].topic == "provider.cleanup"
    assert outbox_events[0].payload == {
        "cleanup_operation_id": str(cleanup.id),
    }
    assert ledgers == []


@pytest.mark.anyio
async def test_canceled_generation_event_cannot_restore_inactive_account(
    async_client,
    client_database_url,
    signed_stripe_headers_factory,
    stripe_subscription_created_payload,
) -> None:
    await _seed_completed_generation_one_deactivation(
        client_database_url,
        email="canceled-id-stale@example.com",
    )
    payload = deepcopy(stripe_subscription_created_payload)
    payload.update(
        id="evt_canceled_id_late_active",
        created=1710000300,
        type="customer.subscription.updated",
    )
    payload["data"]["object"].update(
        id="sub_generation_1",
        created=10,
        status="active",
    )
    payload["data"]["object"]["metadata"]["lifecycle_generation"] = "1"

    response = await _post_stripe_event(
        async_client,
        signed_stripe_headers_factory,
        payload,
    )

    assert response.status_code == 202
    engine = create_async_engine(client_database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        subscription = await session.scalar(select(Subscription))
        user = await session.scalar(select(User))
        outbox_events = list((await session.execute(select(OutboxEvent))).scalars())
    await engine.dispose()

    assert subscription is not None and subscription.status == "canceled"
    assert user is not None and user.status == "inactive"
    assert outbox_events == []


@pytest.mark.anyio
async def test_owner_cancellation_terminal_event_converges_on_exact_incomplete_operation(
    async_client,
    client_database_url,
    signed_stripe_headers_factory,
    stripe_subscription_created_payload,
) -> None:
    requested_at = datetime(2026, 3, 1, tzinfo=UTC)
    engine = create_async_engine(client_database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        user = User(
            clerk_user_id="user_123",
            email="owner-convergence@example.com",
            status="deactivating",
            lifecycle_generation=2,
        )
        session.add(user)
        await session.flush()
        session.add(
            Subscription(
                user_id=user.id,
                stripe_customer_id="cus_123",
                stripe_subscription_id="sub_123",
                plan_tier="starter",
                status="active",
                allocated_minutes=60,
                lifecycle_generation=1,
                stripe_subscription_created_at=datetime.fromtimestamp(
                    1709990000,
                    UTC,
                ),
                last_stripe_event_created_at=datetime.fromtimestamp(
                    1710000000,
                    UTC,
                ),
            )
        )
        operation = AccountDeactivationOperation(
            user_id=user.id,
            lifecycle_generation=2,
            trigger="owner_request",
            stripe_subscription_id="sub_123",
            requested_at=requested_at,
        )
        session.add(operation)
        await session.flush()
        session.add(
            OutboxEvent(
                topic="account.deactivate",
                aggregate_type="account-deactivation-operation",
                aggregate_id=operation.id,
                idempotency_key=f"account.deactivate:{operation.id}",
                payload={"operation_id": str(operation.id)},
            )
        )
        await session.commit()
        operation_id = operation.id
    await engine.dispose()

    payload = deepcopy(stripe_subscription_created_payload)
    payload.update(
        id="evt_owner_terminal_convergence",
        created=1710000200,
        type="customer.subscription.deleted",
    )
    payload["data"]["object"]["status"] = "canceled"
    payload["data"]["object"]["metadata"].pop("lifecycle_generation", None)

    response = await _post_stripe_event(
        async_client,
        signed_stripe_headers_factory,
        payload,
    )

    assert response.status_code == 202
    engine = create_async_engine(client_database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        operations = list(
            (await session.execute(select(AccountDeactivationOperation))).scalars()
        )
        subscription = await session.scalar(select(Subscription))
        outbox_events = list((await session.execute(select(OutboxEvent))).scalars())
    await engine.dispose()

    assert [operation.id for operation in operations] == [operation_id]
    assert operations[0].trigger == "owner_request"
    assert operations[0].routing_disabled_at is None
    assert operations[0].subscription_canceled_at is None
    assert subscription is not None and subscription.status == "canceled"
    assert len(outbox_events) == 1
    assert outbox_events[0].payload == {"operation_id": str(operation_id)}


@pytest.mark.anyio
@pytest.mark.parametrize("metadata_generation", [None, "1"])
async def test_missing_or_old_generation_invoice_cannot_grant_or_enable(
    async_client,
    client_database_url,
    signed_stripe_headers_factory,
    stripe_invoice_paid_payload,
    metadata_generation: str | None,
) -> None:
    engine = create_async_engine(client_database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        user = User(
            clerk_user_id="user_123",
            email=f"stale-invoice-{metadata_generation}@example.com",
            lifecycle_generation=2,
        )
        session.add(user)
        await session.flush()
        session.add(
            Subscription(
                user_id=user.id,
                stripe_customer_id="cus_123",
                stripe_subscription_id="sub_123",
                plan_tier="starter",
                status="active",
                allocated_minutes=60,
                lifecycle_generation=2,
                stripe_subscription_created_at=datetime.fromtimestamp(10, UTC),
                last_stripe_event_created_at=datetime.fromtimestamp(20, UTC),
            )
        )
        session.add(
            PhoneNumber(
                user_id=user.id,
                e164="+35315550172",
                country_code="IE",
                provider="telnyx",
                provider_number_id="pn_stale_invoice",
                provider_connection_name="app-disabled",
                is_active=False,
            )
        )
        await session.commit()
    await engine.dispose()

    payload = deepcopy(stripe_invoice_paid_payload)
    payload.update(
        id=f"evt_stale_invoice_{metadata_generation}",
        created=1710000300,
    )
    metadata = payload["data"]["object"]["parent"]["subscription_details"].setdefault(
        "metadata",
        {},
    )
    metadata.update(clerk_user_id="user_123", plan_tier="starter")
    if metadata_generation is not None:
        metadata["lifecycle_generation"] = metadata_generation

    response = await _post_stripe_event(
        async_client,
        signed_stripe_headers_factory,
        payload,
    )

    assert response.status_code == 202
    engine = create_async_engine(client_database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        ledgers = list((await session.execute(select(UsageLedger))).scalars())
        outbox_events = list((await session.execute(select(OutboxEvent))).scalars())
        phone = await session.scalar(select(PhoneNumber))
    await engine.dispose()

    assert ledgers == []
    assert outbox_events == []
    assert phone is not None and phone.is_active is False


@pytest.mark.anyio
async def test_current_generation_paid_invoice_retries_until_subscription_reactivates(
    async_client,
    client_database_url,
    signed_stripe_headers_factory,
    stripe_subscription_created_payload,
    stripe_invoice_paid_payload,
) -> None:
    user_id = await _seed_completed_generation_one_deactivation(
        client_database_url,
        email="invoice-before-subscription-paid@example.com",
    )
    invoice = deepcopy(stripe_invoice_paid_payload)
    invoice.update(id="evt_generation_2_invoice_paid_first", created=1710000400)
    invoice["data"]["object"].update(id="in_generation_2_paid")
    invoice["data"]["object"]["parent"]["subscription_details"].update(
        subscription="sub_generation_2_invoice_first",
        metadata={
            "clerk_user_id": "user_123",
            "user_id": str(user_id),
            "plan_tier": "starter",
            "lifecycle_generation": "2",
        },
    )
    invoice["data"]["object"]["lines"]["data"][0]["parent"][
        "subscription_item_details"
    ]["subscription"] = "sub_generation_2_invoice_first"

    premature = await _post_stripe_event(
        async_client,
        signed_stripe_headers_factory,
        invoice,
    )

    assert premature.status_code == 503
    engine = create_async_engine(client_database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        assert (
            await session.scalar(
                select(WebhookEvent).where(
                    WebhookEvent.external_event_id
                    == "evt_generation_2_invoice_paid_first"
                )
            )
            is None
        )
        assert await session.scalar(select(UsageLedger)) is None
    await engine.dispose()

    subscription = deepcopy(stripe_subscription_created_payload)
    subscription.update(id="evt_generation_2_after_paid_invoice", created=1710000300)
    subscription["data"]["object"].update(
        id="sub_generation_2_invoice_first",
        created=1710000200,
        status="active",
    )
    subscription["data"]["object"]["metadata"].update(
        user_id=str(user_id),
        lifecycle_generation="2",
    )
    activated = await _post_stripe_event(
        async_client,
        signed_stripe_headers_factory,
        subscription,
    )
    retried = await _post_stripe_event(
        async_client,
        signed_stripe_headers_factory,
        invoice,
    )
    replayed = await _post_stripe_event(
        async_client,
        signed_stripe_headers_factory,
        invoice,
    )

    assert activated.status_code == retried.status_code == replayed.status_code == 202
    engine = create_async_engine(client_database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        current_subscription = await session.scalar(select(Subscription))
        current_user = await session.scalar(select(User))
        ledgers = list((await session.execute(select(UsageLedger))).scalars())
        outbox_events = list((await session.execute(select(OutboxEvent))).scalars())
    await engine.dispose()

    assert current_subscription is not None
    assert current_subscription.stripe_subscription_id == (
        "sub_generation_2_invoice_first"
    )
    assert current_subscription.lifecycle_generation == 2
    assert current_subscription.status == "active"
    assert current_user is not None and current_user.status == "active"
    assert [ledger.source_id for ledger in ledgers] == ["in_generation_2_paid"]
    assert outbox_events == []


@pytest.mark.anyio
async def test_current_generation_failed_invoice_retries_until_subscription_reactivates(
    async_client,
    client_database_url,
    signed_stripe_headers_factory,
    stripe_subscription_created_payload,
    stripe_invoice_paid_payload,
) -> None:
    user_id = await _seed_completed_generation_one_deactivation(
        client_database_url,
        email="invoice-before-subscription-failed@example.com",
    )
    invoice = deepcopy(stripe_invoice_paid_payload)
    invoice.update(
        id="evt_generation_2_invoice_failed_first",
        created=1710000400,
        type="invoice.payment_failed",
    )
    invoice["data"]["object"].update(id="in_generation_2_failed", status="open")
    invoice["data"]["object"]["parent"]["subscription_details"].update(
        subscription="sub_generation_2_invoice_failed_first",
        metadata={
            "clerk_user_id": "user_123",
            "user_id": str(user_id),
            "plan_tier": "starter",
            "lifecycle_generation": "2",
        },
    )
    invoice["data"]["object"]["lines"]["data"][0]["parent"][
        "subscription_item_details"
    ]["subscription"] = "sub_generation_2_invoice_failed_first"

    premature = await _post_stripe_event(
        async_client,
        signed_stripe_headers_factory,
        invoice,
    )

    assert premature.status_code == 503
    engine = create_async_engine(client_database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        assert (
            await session.scalar(
                select(WebhookEvent).where(
                    WebhookEvent.external_event_id
                    == "evt_generation_2_invoice_failed_first"
                )
            )
            is None
        )
        assert await session.scalar(select(OutboxEvent)) is None
    await engine.dispose()

    subscription = deepcopy(stripe_subscription_created_payload)
    subscription.update(
        id="evt_generation_2_after_failed_invoice",
        created=1710000300,
    )
    subscription["data"]["object"].update(
        id="sub_generation_2_invoice_failed_first",
        created=1710000200,
        status="active",
    )
    subscription["data"]["object"]["metadata"].update(
        user_id=str(user_id),
        lifecycle_generation="2",
    )
    activated = await _post_stripe_event(
        async_client,
        signed_stripe_headers_factory,
        subscription,
    )
    retried = await _post_stripe_event(
        async_client,
        signed_stripe_headers_factory,
        invoice,
    )
    replayed = await _post_stripe_event(
        async_client,
        signed_stripe_headers_factory,
        invoice,
    )

    assert activated.status_code == retried.status_code == replayed.status_code == 202
    engine = create_async_engine(client_database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        current_subscription = await session.scalar(select(Subscription))
        current_user = await session.scalar(select(User))
        ledgers = list((await session.execute(select(UsageLedger))).scalars())
        outbox_events = list((await session.execute(select(OutboxEvent))).scalars())
    await engine.dispose()

    assert current_subscription is not None
    assert current_subscription.stripe_subscription_id == (
        "sub_generation_2_invoice_failed_first"
    )
    assert current_subscription.lifecycle_generation == 2
    assert current_subscription.status == "past_due"
    assert current_user is not None and current_user.status == "active"
    assert ledgers == []
    assert len(outbox_events) == 1
    assert outbox_events[0].topic == "phone.disable"


@pytest.mark.anyio
async def test_stripe_webhook_has_no_telnyx_provider_dependency(
    async_client,
    client_database_url,
    signed_stripe_headers_factory,
    stripe_subscription_created_payload,
) -> None:
    engine = create_async_engine(client_database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        session.add(User(clerk_user_id="user_123", email="stripe-only@example.com"))
        await session.commit()
    await engine.dispose()

    from app.main import app
    from app.providers.telephony.telnyx import get_telephony_provider

    def fail_if_telnyx_is_resolved() -> None:
        raise AssertionError("Stripe webhook must not resolve a Telnyx provider")

    app.dependency_overrides[get_telephony_provider] = fail_if_telnyx_is_resolved
    try:
        response = await async_client.post(
            "/webhooks/stripe",
            content=json.dumps(
                stripe_subscription_created_payload,
                separators=(",", ":"),
            ).encode("utf-8"),
            headers=signed_stripe_headers_factory(stripe_subscription_created_payload),
        )
    finally:
        app.dependency_overrides.pop(get_telephony_provider, None)

    assert response.status_code == 202


@pytest.mark.anyio
async def test_first_paid_invoice_grants_minutes_without_ordering_number(
    async_client,
    client_database_url,
    signed_stripe_headers_factory,
    stripe_invoice_paid_payload,
) -> None:
    async def seed_user() -> None:
        engine = create_async_engine(client_database_url, future=True)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session:
            session.add(User(clerk_user_id="user_123", email="billing@example.com"))
            await session.commit()
        await engine.dispose()

    async def fetch_state() -> tuple[
        list[Subscription],
        list[Notification],
        list[UsageLedger],
        list[PhoneNumber],
        list[OutboxEvent],
    ]:
        engine = create_async_engine(client_database_url, future=True)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session:
            subscriptions = list(
                (await session.execute(select(Subscription))).scalars()
            )
            notifications = list(
                (await session.execute(select(Notification))).scalars()
            )
            ledgers = list((await session.execute(select(UsageLedger))).scalars())
            phone_numbers = list((await session.execute(select(PhoneNumber))).scalars())
            outbox_events = list((await session.execute(select(OutboxEvent))).scalars())
        await engine.dispose()
        return subscriptions, notifications, ledgers, phone_numbers, outbox_events

    await seed_user()

    invoice_payload = json.loads(json.dumps(stripe_invoice_paid_payload))
    invoice_payload["data"]["object"]["lines"]["data"][0]["price"] = {
        "lookup_key": "starter"
    }
    invoice_payload["data"]["object"]["parent"]["subscription_details"]["metadata"] = {
        "clerk_user_id": "user_123",
        "plan_tier": "starter",
    }

    from app.main import app

    pool = MockArqPool()
    app.state.arq_pool = pool

    response = await async_client.post(
        "/webhooks/stripe",
        content=json.dumps(invoice_payload, separators=(",", ":")).encode("utf-8"),
        headers=signed_stripe_headers_factory(invoice_payload),
    )

    assert response.status_code == 202

    assert pool.enqueued_jobs == []

    (
        subscriptions,
        notifications,
        ledgers,
        phone_numbers,
        outbox_events,
    ) = await fetch_state()

    assert response.status_code == 202
    assert subscriptions[0].plan_tier == "starter"
    assert notifications == []
    assert ledgers[0].event_type == "subscription_activated"
    assert ledgers[0].minutes_delta == 60
    assert ledgers[0].balance_after == 60
    assert not phone_numbers
    assert outbox_events == []


@pytest.mark.anyio
async def test_invoice_paid_resets_minutes(
    async_client,
    client_database_url,
    signed_stripe_headers_factory,
    stripe_invoice_paid_payload,
) -> None:
    async def seed_subscription() -> None:
        engine = create_async_engine(client_database_url, future=True)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session:
            user = User(clerk_user_id="user_123", email="billing@example.com")
            session.add(user)
            await session.flush()
            session.add(
                Subscription(
                    user_id=user.id,
                    stripe_customer_id="cus_123",
                    stripe_subscription_id="sub_123",
                    plan_tier="starter",
                    status="active",
                    allocated_minutes=60,
                    current_period_start=datetime.fromtimestamp(1710000000, UTC),
                    current_period_end=datetime.fromtimestamp(1712592000, UTC),
                    stripe_subscription_created_at=datetime.fromtimestamp(
                        1709990000,
                        UTC,
                    ),
                    last_stripe_event_created_at=datetime.fromtimestamp(
                        1710000100,
                        UTC,
                    ),
                )
            )
            session.add(
                UsageLedger(
                    user_id=user.id,
                    event_type="subscription_activated",
                    minutes_delta=120,
                    balance_after=120,
                )
            )
            session.add(
                PhoneNumber(
                    user_id=user.id,
                    e164="+35315550101",
                    country_code="IE",
                    provider="telnyx",
                    provider_number_id="pn_existing_renewal",
                    provider_connection_name="app-disabled",
                    is_active=False,
                )
            )
            await session.commit()
        await engine.dispose()

    async def fetch_state() -> tuple[
        list[UsageLedger],
        list[OutboxEvent],
        PhoneNumber,
    ]:
        engine = create_async_engine(client_database_url, future=True)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session:
            result = await session.execute(
                select(UsageLedger).order_by(UsageLedger.created_at.asc())
            )
            rows = list(result.scalars())
            outbox_events = list((await session.execute(select(OutboxEvent))).scalars())
            phone = (await session.execute(select(PhoneNumber))).scalar_one()
        await engine.dispose()
        return rows, outbox_events, phone

    await seed_subscription()

    response = await async_client.post(
        "/webhooks/stripe",
        content=json.dumps(stripe_invoice_paid_payload, separators=(",", ":")).encode(
            "utf-8"
        ),
        headers=signed_stripe_headers_factory(stripe_invoice_paid_payload),
    )

    assert response.status_code == 202
    ledgers, outbox_events, phone = await fetch_state()
    assert ledgers[-1].event_type == "invoice_paid_reset"
    assert outbox_events == []
    assert phone.is_active is False


@pytest.mark.anyio
async def test_invoice_paid_bootstraps_subscription_without_ordering_number(
    async_client,
    client_database_url,
    signed_stripe_headers_factory,
    stripe_invoice_paid_payload,
) -> None:
    async def seed_user() -> None:
        engine = create_async_engine(client_database_url, future=True)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session:
            session.add(User(clerk_user_id="user_123", email="billing@example.com"))
            await session.commit()
        await engine.dispose()

    async def fetch_state() -> tuple[
        list[Subscription],
        list[UsageLedger],
        list[OutboxEvent],
    ]:
        engine = create_async_engine(client_database_url, future=True)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session:
            subscriptions = list(
                (await session.execute(select(Subscription))).scalars()
            )
            ledgers = list((await session.execute(select(UsageLedger))).scalars())
            outbox_events = list((await session.execute(select(OutboxEvent))).scalars())
        await engine.dispose()
        return subscriptions, ledgers, outbox_events

    await seed_user()

    invoice_payload = json.loads(json.dumps(stripe_invoice_paid_payload))
    invoice_payload["data"]["object"].pop("paid")
    invoice_payload["data"]["object"]["lines"]["data"][0]["price"] = {
        "lookup_key": "starter"
    }
    invoice_payload["data"]["object"]["parent"]["subscription_details"]["metadata"] = {
        "clerk_user_id": "user_123",
        "plan_tier": "starter",
    }

    from app.main import app

    pool = MockArqPool()
    app.state.arq_pool = pool

    response = await async_client.post(
        "/webhooks/stripe",
        content=json.dumps(invoice_payload, separators=(",", ":")).encode("utf-8"),
        headers=signed_stripe_headers_factory(invoice_payload),
    )

    assert response.status_code == 202

    subscriptions, ledgers, outbox_events = await fetch_state()
    assert subscriptions[0].stripe_subscription_id == "sub_123"
    assert subscriptions[0].plan_tier == "starter"
    assert subscriptions[0].status == "active"
    assert subscriptions[0].allocated_minutes == 60
    assert ledgers[-1].event_type == "subscription_activated"
    assert ledgers[-1].minutes_delta == 60
    assert ledgers[-1].balance_after == 60
    assert outbox_events == []
    assert pool.enqueued_jobs == []


@pytest.mark.anyio
async def test_distinct_webhook_events_grant_one_invoice_without_ordering_number(
    async_client,
    client_database_url,
    signed_stripe_headers_factory,
    stripe_invoice_paid_payload,
) -> None:
    engine = create_async_engine(client_database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        session.add(User(clerk_user_id="user_123", email="invoice-once@example.com"))
        await session.commit()
    await engine.dispose()

    first_payload = deepcopy(stripe_invoice_paid_payload)
    first_payload["data"]["object"]["lines"]["data"][0]["price"] = {
        "lookup_key": "starter"
    }
    first_payload["data"]["object"]["parent"]["subscription_details"]["metadata"] = {
        "clerk_user_id": "user_123",
        "plan_tier": "starter",
    }
    second_payload = deepcopy(first_payload)
    second_payload["id"] = "evt_invoice_paid_duplicate_delivery"
    second_payload["created"] += 1

    from app.main import app

    pool = MockArqPool()
    app.state.arq_pool = pool

    first_response = await _post_stripe_event(
        async_client,
        signed_stripe_headers_factory,
        first_payload,
    )
    second_response = await _post_stripe_event(
        async_client,
        signed_stripe_headers_factory,
        second_payload,
    )

    engine = create_async_engine(client_database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        ledgers = list(
            (
                await session.execute(
                    select(UsageLedger).order_by(UsageLedger.created_at.asc())
                )
            ).scalars()
        )
        outbox_events = list((await session.execute(select(OutboxEvent))).scalars())
    await engine.dispose()

    assert first_response.status_code == second_response.status_code == 202
    assert len(ledgers) == 1
    assert ledgers[0].event_type == "subscription_activated"
    assert ledgers[0].source_id == first_payload["data"]["object"]["id"]
    assert outbox_events == []
    assert pool.enqueued_jobs == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("event_type", "expected_status", "expected_outbox", "expected_ledger"),
    [
        ("customer.subscription.created", "active", 0, 0),
        ("customer.subscription.updated", "past_due", 1, 0),
        ("customer.subscription.deleted", "canceled", 1, 0),
        ("invoice.paid", "active", 0, 1),
        ("invoice.payment_failed", "past_due", 1, 0),
    ],
)
async def test_every_supported_stripe_lifecycle_event_is_replay_safe_without_provisioning(
    async_client,
    client_database_url,
    signed_stripe_headers_factory,
    stripe_subscription_created_payload,
    stripe_invoice_paid_payload,
    event_type: str,
    expected_status: str,
    expected_outbox: int,
    expected_ledger: int,
) -> None:
    engine = create_async_engine(client_database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        user = User(clerk_user_id="user_123", email="lifecycle@example.com")
        session.add(user)
        await session.flush()
        if event_type != "customer.subscription.created":
            session.add(
                Subscription(
                    user_id=user.id,
                    stripe_customer_id="cus_123",
                    stripe_subscription_id="sub_123",
                    plan_tier="starter",
                    status="active",
                    allocated_minutes=60,
                    stripe_subscription_created_at=datetime.fromtimestamp(
                        1709990000,
                        UTC,
                    ),
                    last_stripe_event_created_at=datetime.fromtimestamp(
                        1710000000,
                        UTC,
                    ),
                )
            )
        if event_type in {
            "customer.subscription.deleted",
            "invoice.payment_failed",
        }:
            session.add(
                PhoneNumber(
                    user_id=user.id,
                    e164="+35315550103",
                    country_code="IE",
                    provider="telnyx",
                    provider_number_id="pn_existing_payment_reconciliation",
                    provider_connection_name="app-active",
                    is_active=True,
                )
            )
        await session.commit()
    await engine.dispose()

    event_id = "evt_" + event_type.replace(".", "_")
    if event_type.startswith("customer.subscription"):
        payload = deepcopy(stripe_subscription_created_payload)
        payload["id"] = event_id
        payload["type"] = event_type
        payload["data"]["object"]["status"] = expected_status
    else:
        payload = deepcopy(stripe_invoice_paid_payload)
        payload["id"] = event_id
        payload["type"] = event_type
        if event_type == "invoice.payment_failed":
            payload["data"]["object"]["status"] = "open"
            payload["data"]["object"]["paid"] = False

    from app.main import app

    pool = MockArqPool()
    app.state.arq_pool = pool

    first = await async_client.post(
        "/webhooks/stripe",
        content=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        headers=signed_stripe_headers_factory(payload),
    )
    replay = await async_client.post(
        "/webhooks/stripe",
        content=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        headers=signed_stripe_headers_factory(payload),
    )

    assert first.status_code == 202
    assert replay.status_code == 202
    expected_jobs: list[tuple[str, dict[str, object]]] = (
        [("outbox_delivery_job", {})] if expected_outbox else []
    )
    assert pool.enqueued_jobs == expected_jobs

    engine = create_async_engine(client_database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        subscription = (await session.execute(select(Subscription))).scalar_one()
        user = (await session.execute(select(User))).scalar_one()
        operation = await session.scalar(select(AccountDeactivationOperation))
        webhook_events = list(
            (
                await session.execute(
                    select(WebhookEvent).where(
                        WebhookEvent.external_event_id == event_id
                    )
                )
            ).scalars()
        )
        outbox_events = list((await session.execute(select(OutboxEvent))).scalars())
        ledgers = list((await session.execute(select(UsageLedger))).scalars())
    await engine.dispose()

    assert subscription.status == expected_status
    assert len(webhook_events) == 1
    assert len(outbox_events) == expected_outbox
    assert len(ledgers) == expected_ledger
    assert all(intent.topic != "phone.provision" for intent in outbox_events)
    if outbox_events:
        intent = outbox_events[0]
        if event_type == "customer.subscription.deleted":
            assert operation is not None
            assert user.status == "deactivating"
            assert intent.topic == "account.deactivate"
            assert intent.aggregate_type == "account-deactivation-operation"
            assert intent.aggregate_id == operation.id
            assert intent.payload == {"operation_id": str(operation.id)}
        else:
            assert intent.topic == "phone.disable"
            assert intent.aggregate_type == "user"
            assert intent.aggregate_id == subscription.user_id
            assert intent.idempotency_key == f"stripe:{event_type}:{event_id}"


@pytest.mark.anyio
async def test_invoice_payment_does_not_enable_phone_or_require_redis_wakeup(
    async_client,
    client_database_url,
    signed_stripe_headers_factory,
    stripe_invoice_paid_payload,
) -> None:
    class FailingPool:
        async def enqueue_job(self, _name, _payload):
            raise ConnectionError("redis unavailable")

    engine = create_async_engine(client_database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        user = User(clerk_user_id="user_123", email="redis-down@example.com")
        session.add(user)
        await session.flush()
        session.add(
            PhoneNumber(
                user_id=user.id,
                e164="+35315550102",
                country_code="IE",
                provider="telnyx",
                provider_number_id="pn_existing_redis_failure",
                provider_connection_name="app-disabled",
                is_active=False,
            )
        )
        await session.commit()
    await engine.dispose()

    payload = deepcopy(stripe_invoice_paid_payload)
    payload["data"]["object"]["lines"]["data"][0]["price"] = {"lookup_key": "starter"}
    payload["data"]["object"]["parent"]["subscription_details"]["metadata"] = {
        "clerk_user_id": "user_123",
        "plan_tier": "starter",
    }
    from app.main import app

    app.state.arq_pool = FailingPool()
    response = await _post_stripe_event(
        async_client,
        signed_stripe_headers_factory,
        payload,
    )

    assert response.status_code == 202
    engine = create_async_engine(client_database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        event = await session.scalar(select(OutboxEvent))
        phone = await session.scalar(select(PhoneNumber))
        assert event is None
        assert phone is not None
        assert phone.is_active is False
    await engine.dispose()


def test_stripe_handler_map_lists_every_supported_lifecycle_event() -> None:
    from app.services.billing_service import STRIPE_EVENT_HANDLER_MAP

    assert set(STRIPE_EVENT_HANDLER_MAP) == {
        "customer.subscription.created",
        "customer.subscription.updated",
        "customer.subscription.deleted",
        "invoice.paid",
        "invoice.payment_failed",
    }


@pytest.mark.anyio
async def test_invoice_paid_does_not_grant_when_invoice_policy_rejects(
    async_client,
    client_database_url,
    signed_stripe_headers_factory,
    stripe_invoice_paid_payload,
) -> None:
    engine = create_async_engine(client_database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        user = User(clerk_user_id="user_123", email="invoice-policy@example.com")
        session.add(user)
        await session.flush()
        session.add(
            Subscription(
                user_id=user.id,
                stripe_customer_id="cus_123",
                stripe_subscription_id="sub_123",
                plan_tier="starter",
                status="active",
                allocated_minutes=60,
            )
        )
        await session.commit()
    await engine.dispose()

    payload = deepcopy(stripe_invoice_paid_payload)
    payload["id"] = "evt_invoice_policy_rejected"
    payload["data"]["object"]["status"] = "open"
    payload["data"]["object"]["paid"] = False

    response = await async_client.post(
        "/webhooks/stripe",
        content=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        headers=signed_stripe_headers_factory(payload),
    )

    assert response.status_code == 202
    engine = create_async_engine(client_database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        assert await session.scalar(select(UsageLedger)) is None
    await engine.dispose()


@pytest.mark.anyio
async def test_subscription_event_rejects_unsupported_lookup_key(
    db_session,
    stripe_subscription_created_payload,
) -> None:
    from app.services.billing_service import (
        BillingService,
        PLAN_MINUTES,
        UnsupportedStripeLifecycleError,
    )

    user = User(clerk_user_id="user_123", email="unsupported-plan@example.com")
    db_session.add(user)
    await db_session.flush()
    payload = deepcopy(stripe_subscription_created_payload)
    payload["id"] = "evt_unsupported_lookup_key"
    payload["data"]["object"]["items"]["data"][0]["price"]["lookup_key"] = "standard"

    assert PLAN_MINUTES == {"starter": 60}
    with pytest.raises(UnsupportedStripeLifecycleError):
        await BillingService(db_session).handle_event(payload)

    await db_session.rollback()
    assert await db_session.scalar(select(Subscription)) is None


@pytest.mark.anyio
@pytest.mark.parametrize("invalid_field", ["plan", "status"])
async def test_unsupported_subscription_data_is_a_safe_bad_request(
    async_client,
    client_database_url,
    signed_stripe_headers_factory,
    stripe_subscription_created_payload,
    caplog,
    invalid_field: str,
) -> None:
    engine = create_async_engine(client_database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        session.add(User(clerk_user_id="user_123", email="bad-stripe-data@example.com"))
        await session.commit()
    await engine.dispose()

    sentinel = f"UNSUPPORTED_STRIPE_{invalid_field.upper()}_SENTINEL"
    payload = deepcopy(stripe_subscription_created_payload)
    payload["id"] = f"evt_unsupported_{invalid_field}"
    if invalid_field == "plan":
        payload["data"]["object"]["items"]["data"][0]["price"]["lookup_key"] = sentinel
    else:
        payload["data"]["object"]["status"] = sentinel

    response = await async_client.post(
        "/webhooks/stripe",
        content=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        headers=signed_stripe_headers_factory(payload),
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "Unsupported Stripe subscription data"}
    assert sentinel not in caplog.text

    engine = create_async_engine(client_database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        assert await session.scalar(select(Subscription)) is None
        assert await session.scalar(select(OutboxEvent)) is None
    await engine.dispose()


def test_stripe_webhook_envelope_requires_top_level_created() -> None:
    from pydantic import ValidationError

    from app.schemas.billing import StripeWebhookEnvelope

    with pytest.raises(ValidationError):
        StripeWebhookEnvelope.model_validate(
            {"id": "evt_missing_created", "type": "invoice.paid", "data": {}}
        )


@pytest.mark.anyio
async def test_reverse_order_same_subscription_update_does_not_regress_status(
    async_client,
    client_database_url,
    signed_stripe_headers_factory,
    stripe_subscription_created_payload,
) -> None:
    engine = create_async_engine(client_database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        session.add(User(clerk_user_id="user_123", email="reverse-order@example.com"))
        await session.commit()
    await engine.dispose()

    newer = deepcopy(stripe_subscription_created_payload)
    newer.update(
        id="evt_update_newer", created=300, type="customer.subscription.updated"
    )
    newer["data"]["object"].update(created=10, status="past_due")
    older = deepcopy(newer)
    older.update(id="evt_update_older", created=200)
    older["data"]["object"]["status"] = "active"

    assert (
        await _post_stripe_event(async_client, signed_stripe_headers_factory, newer)
    ).status_code == 202
    assert (
        await _post_stripe_event(async_client, signed_stripe_headers_factory, older)
    ).status_code == 202

    engine = create_async_engine(client_database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        subscription = await session.scalar(select(Subscription))
        outbox_events = list((await session.execute(select(OutboxEvent))).scalars())
    await engine.dispose()

    assert subscription is not None
    assert subscription.status == "past_due"
    assert subscription.last_stripe_event_created_at is not None
    assert subscription.last_stripe_event_created_at.replace(
        tzinfo=UTC
    ) == datetime.fromtimestamp(300, UTC)
    assert len(outbox_events) == 1


@pytest.mark.anyio
async def test_deleted_subscription_ignores_older_paid_invoice(
    async_client,
    client_database_url,
    signed_stripe_headers_factory,
    stripe_subscription_created_payload,
    stripe_invoice_paid_payload,
) -> None:
    engine = create_async_engine(client_database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        session.add(User(clerk_user_id="user_123", email="deleted-invoice@example.com"))
        await session.commit()
    await engine.dispose()

    created = deepcopy(stripe_subscription_created_payload)
    created.update(id="evt_created_before_delete", created=100)
    created["data"]["object"]["created"] = 10
    deleted = deepcopy(created)
    deleted.update(
        id="evt_delete_newer", created=300, type="customer.subscription.deleted"
    )
    deleted["data"]["object"]["status"] = "canceled"
    invoice = deepcopy(stripe_invoice_paid_payload)
    invoice.update(id="evt_invoice_older_than_delete", created=200)

    assert (
        await _post_stripe_event(async_client, signed_stripe_headers_factory, created)
    ).status_code == 202
    assert (
        await _post_stripe_event(async_client, signed_stripe_headers_factory, deleted)
    ).status_code == 202
    assert (
        await _post_stripe_event(async_client, signed_stripe_headers_factory, invoice)
    ).status_code == 202

    engine = create_async_engine(client_database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        subscription = await session.scalar(select(Subscription))
        ledgers = list((await session.execute(select(UsageLedger))).scalars())
        outbox_events = list((await session.execute(select(OutboxEvent))).scalars())
    await engine.dispose()

    assert subscription is not None
    assert subscription.status == "canceled"
    assert ledgers == []
    assert len(outbox_events) == 1


@pytest.mark.anyio
async def test_deactivating_account_rejects_new_and_old_subscription_events(
    async_client,
    client_database_url,
    signed_stripe_headers_factory,
    stripe_subscription_created_payload,
) -> None:
    engine = create_async_engine(client_database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        session.add(
            User(clerk_user_id="user_123", email="resubscribe-order@example.com")
        )
        await session.commit()
    await engine.dispose()

    old_created = deepcopy(stripe_subscription_created_payload)
    old_created.update(id="evt_old_created", created=100)
    old_created["data"]["object"].update(id="sub_old", created=10)
    old_deleted = deepcopy(old_created)
    old_deleted.update(
        id="evt_old_deleted", created=150, type="customer.subscription.deleted"
    )
    old_deleted["data"]["object"]["status"] = "canceled"
    new_created = deepcopy(stripe_subscription_created_payload)
    new_created.update(id="evt_new_created", created=200)
    new_created["data"]["object"].update(id="sub_new", created=20)
    old_late = deepcopy(old_created)
    old_late.update(
        id="evt_old_late", created=300, type="customer.subscription.updated"
    )
    old_late["data"]["object"]["status"] = "past_due"

    for payload in (old_created, old_deleted, new_created, old_late):
        assert (
            await _post_stripe_event(
                async_client, signed_stripe_headers_factory, payload
            )
        ).status_code == 202

    engine = create_async_engine(client_database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        subscription = await session.scalar(select(Subscription))
        user = await session.scalar(select(User))
        operation = await session.scalar(select(AccountDeactivationOperation))
        cleanup = await session.scalar(select(ProviderCleanupOperation))
        outbox_events = list((await session.execute(select(OutboxEvent))).scalars())
    await engine.dispose()

    assert subscription is not None
    assert subscription.stripe_subscription_id == "sub_old"
    assert subscription.status == "canceled"
    assert subscription.stripe_subscription_created_at is not None
    assert subscription.stripe_subscription_created_at.replace(
        tzinfo=UTC
    ) == datetime.fromtimestamp(10, UTC)
    assert user is not None and user.status == "deactivating"
    assert operation is not None and operation.trigger == "subscription_ended"
    assert cleanup is not None
    assert cleanup.provider_resource_id == "sub_new"
    assert cleanup.resource_type == "stripe_subscription"
    events_by_topic = {event.topic: event for event in outbox_events}
    assert set(events_by_topic) == {"account.deactivate", "provider.cleanup"}
    assert events_by_topic["account.deactivate"].payload == {
        "operation_id": str(operation.id)
    }
    assert events_by_topic["provider.cleanup"].payload == {
        "cleanup_operation_id": str(cleanup.id)
    }


@pytest.mark.anyio
async def test_conflicting_active_subscription_is_adopted_for_cleanup(
    async_client,
    client_database_url,
    signed_stripe_headers_factory,
    stripe_subscription_created_payload,
) -> None:
    engine = create_async_engine(client_database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        user = User(
            clerk_user_id="user_123",
            email="conflicting-subscription@example.com",
        )
        session.add(user)
        await session.flush()
        session.add(
            Subscription(
                user_id=user.id,
                stripe_customer_id="cus_retained",
                stripe_subscription_id="sub_current",
                plan_tier="starter",
                status="active",
                allocated_minutes=60,
                lifecycle_generation=1,
                stripe_subscription_created_at=datetime.fromtimestamp(10, UTC),
                last_stripe_event_created_at=datetime.fromtimestamp(20, UTC),
            )
        )
        await session.commit()
        user_id = user.id
    await engine.dispose()

    payload = deepcopy(stripe_subscription_created_payload)
    payload.update(id="evt_conflicting_active", created=30)
    payload["data"]["object"].update(
        id="sub_conflicting_active",
        created=25,
        status="active",
    )
    payload["data"]["object"]["metadata"].update(
        user_id=str(user_id),
        lifecycle_generation="1",
    )

    response = await _post_stripe_event(
        async_client,
        signed_stripe_headers_factory,
        payload,
    )

    assert response.status_code == 202
    engine = create_async_engine(client_database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        subscription = await session.scalar(select(Subscription))
        cleanup = await session.scalar(select(ProviderCleanupOperation))
        outbox_events = list((await session.execute(select(OutboxEvent))).scalars())
    await engine.dispose()

    assert subscription is not None
    assert subscription.stripe_subscription_id == "sub_current"
    assert subscription.status == "active"
    assert cleanup is not None
    assert cleanup.provider_resource_id == "sub_conflicting_active"
    assert cleanup.resource_type == "stripe_subscription"
    assert len(outbox_events) == 1
    assert outbox_events[0].topic == "provider.cleanup"
    assert outbox_events[0].payload == {
        "cleanup_operation_id": str(cleanup.id),
    }


@pytest.mark.anyio
async def test_equal_second_routing_update_cannot_override_nonrouting_status(
    async_client,
    client_database_url,
    signed_stripe_headers_factory,
    stripe_subscription_created_payload,
) -> None:
    engine = create_async_engine(client_database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        session.add(User(clerk_user_id="user_123", email="equal-second@example.com"))
        await session.commit()
    await engine.dispose()

    created = deepcopy(stripe_subscription_created_payload)
    created.update(id="evt_equal_created", created=100)
    created["data"]["object"]["created"] = 10
    deleted = deepcopy(created)
    deleted.update(
        id="evt_equal_deleted", created=200, type="customer.subscription.deleted"
    )
    deleted["data"]["object"]["status"] = "canceled"
    routing_update = deepcopy(created)
    routing_update.update(
        id="evt_equal_routing", created=200, type="customer.subscription.updated"
    )

    for payload in (created, deleted, routing_update):
        assert (
            await _post_stripe_event(
                async_client, signed_stripe_headers_factory, payload
            )
        ).status_code == 202

    engine = create_async_engine(client_database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        subscription = await session.scalar(select(Subscription))
    await engine.dispose()

    assert subscription is not None
    assert subscription.status == "canceled"


@pytest.mark.anyio
async def test_newer_mismatched_invoice_is_retryable_and_rolls_back(
    async_client,
    client_database_url,
    signed_stripe_headers_factory,
    stripe_subscription_created_payload,
    stripe_invoice_paid_payload,
) -> None:
    engine = create_async_engine(client_database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        session.add(
            User(clerk_user_id="user_123", email="invoice-conflict@example.com")
        )
        await session.commit()
    await engine.dispose()

    current = deepcopy(stripe_subscription_created_payload)
    current.update(id="evt_current_subscription", created=300)
    current["data"]["object"].update(id="sub_new", created=250)
    assert (
        await _post_stripe_event(async_client, signed_stripe_headers_factory, current)
    ).status_code == 202

    conflicting_invoice = deepcopy(stripe_invoice_paid_payload)
    conflicting_invoice.update(id="evt_newer_mismatched_invoice", created=400)
    conflicting_invoice["data"]["object"]["parent"]["subscription_details"].update(
        subscription="sub_unknown",
        metadata={"clerk_user_id": "user_123", "plan_tier": "starter"},
    )
    conflicting_invoice["data"]["object"]["lines"]["data"][0]["parent"][
        "subscription_item_details"
    ]["subscription"] = "sub_unknown"

    response = await _post_stripe_event(
        async_client,
        signed_stripe_headers_factory,
        conflicting_invoice,
    )

    assert response.status_code == 503
    engine = create_async_engine(client_database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        subscription = await session.scalar(select(Subscription))
        conflict_event = await session.scalar(
            select(WebhookEvent).where(
                WebhookEvent.external_event_id == "evt_newer_mismatched_invoice"
            )
        )
        ledgers = list((await session.execute(select(UsageLedger))).scalars())
    await engine.dispose()

    assert subscription is not None
    assert subscription.stripe_subscription_id == "sub_new"
    assert conflict_event is None
    assert ledgers == []


@pytest.mark.anyio
async def test_deactivating_account_ignores_ambiguous_replacement(
    async_client,
    client_database_url,
    signed_stripe_headers_factory,
    stripe_subscription_created_payload,
) -> None:
    engine = create_async_engine(client_database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        session.add(
            User(clerk_user_id="user_123", email="equal-generation@example.com")
        )
        await session.commit()
    await engine.dispose()

    current = deepcopy(stripe_subscription_created_payload)
    current.update(id="evt_equal_generation_current", created=100)
    current["data"]["object"].update(id="sub_equal_current", created=10)
    canceled = deepcopy(current)
    canceled.update(
        id="evt_equal_generation_canceled",
        created=200,
        type="customer.subscription.deleted",
    )
    canceled["data"]["object"]["status"] = "canceled"
    ambiguous = deepcopy(stripe_subscription_created_payload)
    ambiguous.update(
        id="evt_equal_generation_ambiguous",
        created=300,
        type="customer.subscription.created",
    )
    ambiguous["data"]["object"].update(id="sub_equal_other", created=10)

    for payload in (current, canceled):
        response = await _post_stripe_event(
            async_client,
            signed_stripe_headers_factory,
            payload,
        )
        assert response.status_code == 202

    response = await _post_stripe_event(
        async_client,
        signed_stripe_headers_factory,
        ambiguous,
    )

    assert response.status_code == 202
    engine = create_async_engine(client_database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        subscription = await session.scalar(select(Subscription))
        recorded_event = await session.scalar(
            select(WebhookEvent).where(
                WebhookEvent.external_event_id == "evt_equal_generation_ambiguous"
            )
        )
    await engine.dispose()

    assert subscription is not None
    assert subscription.stripe_subscription_id == "sub_equal_current"
    assert subscription.status == "canceled"
    assert recorded_event is not None


@pytest.mark.anyio
async def test_sparse_new_subscription_does_not_inherit_terminal_subscription_data(
    async_client,
    client_database_url,
    signed_stripe_headers_factory,
    stripe_subscription_created_payload,
) -> None:
    engine = create_async_engine(client_database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        user = User(clerk_user_id="user_123", email="sparse-replacement@example.com")
        session.add(user)
        await session.flush()
        session.add(
            Subscription(
                user_id=user.id,
                stripe_customer_id="cus_terminal",
                stripe_subscription_id="sub_terminal",
                plan_tier="starter",
                status="canceled",
                allocated_minutes=60,
                current_period_start=datetime(2026, 1, 1, tzinfo=UTC),
                current_period_end=datetime(2026, 2, 1, tzinfo=UTC),
                stripe_subscription_created_at=datetime.fromtimestamp(10, UTC),
                last_stripe_event_created_at=datetime.fromtimestamp(20, UTC),
            )
        )
        await session.commit()
    await engine.dispose()

    malformed = deepcopy(stripe_subscription_created_payload)
    malformed.update(id="evt_sparse_replacement", created=300)
    malformed["data"]["object"].update(
        id="sub_sparse_replacement",
        created=200,
        customer=None,
    )
    malformed["data"]["object"]["items"]["data"][0]["price"]["lookup_key"] = None
    malformed["data"]["object"]["metadata"].pop("plan_tier", None)

    response = await _post_stripe_event(
        async_client,
        signed_stripe_headers_factory,
        malformed,
    )

    assert response.status_code == 400
    engine = create_async_engine(client_database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        subscription = await session.scalar(select(Subscription))
        malformed_event = await session.scalar(
            select(WebhookEvent).where(
                WebhookEvent.external_event_id == "evt_sparse_replacement"
            )
        )
    await engine.dispose()

    assert subscription is not None
    assert subscription.stripe_customer_id == "cus_terminal"
    assert subscription.stripe_subscription_id == "sub_terminal"
    assert subscription.plan_tier == "starter"
    assert subscription.status == "canceled"
    assert subscription.allocated_minutes == 60
    assert subscription.current_period_start == datetime(2026, 1, 1)
    assert subscription.current_period_end == datetime(2026, 2, 1)
    assert malformed_event is None


@pytest.mark.anyio
async def test_delayed_cancellation_revokes_legacy_active_subscription(
    async_client,
    client_database_url,
    signed_stripe_headers_factory,
    stripe_subscription_created_payload,
) -> None:
    engine = create_async_engine(client_database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        user = User(clerk_user_id="user_123", email="legacy-cancel@example.com")
        session.add(user)
        await session.flush()
        session.add(
            Subscription(
                user_id=user.id,
                stripe_customer_id="cus_123",
                stripe_subscription_id="sub_123",
                plan_tier="starter",
                status="active",
                allocated_minutes=60,
                stripe_subscription_created_at=None,
                last_stripe_event_created_at=None,
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
                updated_at=datetime(2026, 4, 1, tzinfo=UTC),
            )
        )
        await session.commit()
    await engine.dispose()

    delayed = deepcopy(stripe_subscription_created_payload)
    delayed.update(
        id="evt_delayed_legacy_cancel",
        created=100,
        type="customer.subscription.deleted",
    )
    delayed["data"]["object"].update(created=10, status="canceled")

    response = await _post_stripe_event(
        async_client,
        signed_stripe_headers_factory,
        delayed,
    )

    assert response.status_code == 202
    engine = create_async_engine(client_database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        subscription = await session.scalar(select(Subscription))
        outbox_events = list((await session.execute(select(OutboxEvent))).scalars())
    await engine.dispose()

    assert subscription is not None
    assert subscription.status == "canceled"
    assert subscription.last_stripe_event_created_at is not None
    assert subscription.last_stripe_event_created_at.replace(
        tzinfo=UTC
    ) == datetime.fromtimestamp(100, UTC)
    assert len(outbox_events) == 1


@pytest.mark.anyio
async def test_delayed_invoice_failure_revokes_legacy_active_subscription(
    async_client,
    client_database_url,
    signed_stripe_headers_factory,
    stripe_invoice_paid_payload,
) -> None:
    engine = create_async_engine(client_database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        user = User(
            clerk_user_id="user_123", email="legacy-invoice-failure@example.com"
        )
        session.add(user)
        await session.flush()
        session.add(
            Subscription(
                user_id=user.id,
                stripe_customer_id="cus_123",
                stripe_subscription_id="sub_123",
                plan_tier="starter",
                status="active",
                allocated_minutes=60,
                stripe_subscription_created_at=None,
                last_stripe_event_created_at=None,
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
                updated_at=datetime(2026, 4, 1, tzinfo=UTC),
            )
        )
        await session.commit()
    await engine.dispose()

    delayed = deepcopy(stripe_invoice_paid_payload)
    delayed.update(
        id="evt_delayed_legacy_invoice_failure",
        created=100,
        type="invoice.payment_failed",
    )
    delayed["data"]["object"].update(status="open", paid=False)

    response = await _post_stripe_event(
        async_client,
        signed_stripe_headers_factory,
        delayed,
    )

    assert response.status_code == 202
    engine = create_async_engine(client_database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        subscription = await session.scalar(select(Subscription))
        outbox_events = list((await session.execute(select(OutboxEvent))).scalars())
    await engine.dispose()

    assert subscription is not None
    assert subscription.status == "past_due"
    assert subscription.last_stripe_event_created_at is not None
    assert subscription.last_stripe_event_created_at.replace(
        tzinfo=UTC
    ) == datetime.fromtimestamp(100, UTC)
    assert len(outbox_events) == 1
