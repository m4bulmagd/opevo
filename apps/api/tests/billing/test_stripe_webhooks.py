import json
from copy import deepcopy
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.notification import Notification
from app.models.outbox_event import OutboxEvent
from app.models.phone_number import PhoneNumber
from app.models.subscription import Subscription
from app.models.user import User
from app.models.usage_ledger import UsageLedger
from app.models.webhook_event import WebhookEvent

from tests.fakes import MockArqPool, ReviewRequiredTelephonyProvider


@pytest.mark.anyio
async def test_invalid_signature_is_rejected(
    async_client,
    stripe_subscription_created_payload,
) -> None:
    response = await async_client.post(
        "/webhooks/stripe",
        content=json.dumps(stripe_subscription_created_payload, separators=(",", ":")).encode("utf-8"),
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
            select(WebhookEvent).where(
                WebhookEvent.external_event_id == payload["id"]
            )
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



    async def fetch_state() -> tuple[list[Subscription], list[UsageLedger], list[PhoneNumber]]:
        engine = create_async_engine(client_database_url, future=True)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session:
            subscriptions = list((await session.execute(select(Subscription))).scalars())
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
        content=json.dumps(stripe_subscription_created_payload, separators=(",", ":")).encode("utf-8"),
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
        content=json.dumps(stripe_subscription_created_payload, separators=(",", ":")).encode("utf-8"),
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
        content=json.dumps(stripe_current_subscription_created_payload, separators=(",", ":")).encode("utf-8"),
        headers=signed_stripe_headers_factory(stripe_current_subscription_created_payload),
    )
    assert response.status_code == 202


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
            headers=signed_stripe_headers_factory(
                stripe_subscription_created_payload
            ),
        )
    finally:
        app.dependency_overrides.pop(get_telephony_provider, None)

    assert response.status_code == 202


@pytest.mark.anyio
async def test_subscription_activation_persists_subscription_and_support_notification_when_provisioning_needs_review(
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



    async def fetch_state() -> tuple[list[Subscription], list[Notification], list[UsageLedger], list[PhoneNumber]]:
        engine = create_async_engine(client_database_url, future=True)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session:
            subscriptions = list((await session.execute(select(Subscription))).scalars())
            notifications = list((await session.execute(select(Notification))).scalars())
            ledgers = list((await session.execute(select(UsageLedger))).scalars())
            phone_numbers = list((await session.execute(select(PhoneNumber))).scalars())
        await engine.dispose()
        return subscriptions, notifications, ledgers, phone_numbers

    await seed_user()

    invoice_payload = json.loads(json.dumps(stripe_invoice_paid_payload))
    invoice_payload["data"]["object"]["lines"]["data"][0]["price"] = {"lookup_key": "starter"}
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
    
    assert len(pool.enqueued_jobs) == 1
    assert pool.enqueued_jobs[0][0] == "phone_provisioning_job"
    
    from app.workers.jobs.phone_provisioning import phone_provisioning_job
    
    engine = create_async_engine(client_database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    
    await phone_provisioning_job({
        "telephony_provider": ReviewRequiredTelephonyProvider(),
        "session_factory": session_factory
    }, pool.enqueued_jobs[0][1])
    
    await engine.dispose()

    subscriptions, notifications, ledgers, phone_numbers = await fetch_state()

    assert response.status_code == 202
    assert subscriptions[0].plan_tier == "starter"
    assert notifications[0].notification_type == "phone_number_provisioning_review_required"
    assert notifications[0].payload["contact_support"] is True
    assert ledgers[0].event_type == "subscription_activated"
    assert not phone_numbers


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
            await session.commit()
        await engine.dispose()

    async def fetch_ledgers() -> list[UsageLedger]:
        engine = create_async_engine(client_database_url, future=True)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session:
            result = await session.execute(select(UsageLedger).order_by(UsageLedger.created_at.asc()))
            rows = list(result.scalars())
        await engine.dispose()
        return rows

    await seed_subscription()

    response = await async_client.post(
        "/webhooks/stripe",
        content=json.dumps(stripe_invoice_paid_payload, separators=(",", ":")).encode("utf-8"),
        headers=signed_stripe_headers_factory(stripe_invoice_paid_payload),
    )

    assert response.status_code == 202
    assert (await fetch_ledgers())[-1].event_type == "invoice_paid_reset"


@pytest.mark.anyio
async def test_invoice_paid_bootstraps_subscription_activation_and_enqueues_provisioning(
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

    async def fetch_state() -> tuple[list[Subscription], list[UsageLedger]]:
        engine = create_async_engine(client_database_url, future=True)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session:
            subscriptions = list((await session.execute(select(Subscription))).scalars())
            ledgers = list((await session.execute(select(UsageLedger))).scalars())
        await engine.dispose()
        return subscriptions, ledgers

    await seed_user()

    invoice_payload = json.loads(json.dumps(stripe_invoice_paid_payload))
    invoice_payload["data"]["object"]["lines"]["data"][0]["price"] = {"lookup_key": "starter"}
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

    subscriptions, ledgers = await fetch_state()
    assert subscriptions[0].stripe_subscription_id == "sub_123"
    assert subscriptions[0].plan_tier == "starter"
    assert subscriptions[0].status == "active"
    assert ledgers[-1].event_type == "subscription_activated"
    assert len(pool.enqueued_jobs) == 1
    assert pool.enqueued_jobs[0][0] == "phone_provisioning_job"


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
async def test_every_supported_stripe_lifecycle_event_is_replay_safe(
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
    app.state.arq_pool = None if event_type == "invoice.paid" else pool

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
    assert pool.enqueued_jobs == []

    engine = create_async_engine(client_database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        subscription = (await session.execute(select(Subscription))).scalar_one()
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
    if outbox_events:
        intent = outbox_events[0]
        assert intent.topic == "phone.disable"
        assert intent.aggregate_type == "subscription"
        assert intent.aggregate_id == subscription.id
        assert intent.idempotency_key == f"stripe:{event_type}:{event_id}"


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
