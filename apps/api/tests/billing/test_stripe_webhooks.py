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


async def _post_stripe_event(async_client, signed_stripe_headers_factory, payload: dict):
    return await async_client.post(
        "/webhooks/stripe",
        content=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        headers=signed_stripe_headers_factory(payload),
    )


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
    assert pool.enqueued_jobs[0][0] == "outbox_delivery_job"
    
    from app.workers.jobs.outbox_delivery import outbox_delivery_job
    
    engine = create_async_engine(client_database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    
    await outbox_delivery_job({
        "telephony_provider": ReviewRequiredTelephonyProvider(),
        "session_factory": session_factory
    })
    
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
    assert pool.enqueued_jobs[0][0] == "outbox_delivery_job"


@pytest.mark.anyio
async def test_distinct_webhook_events_for_one_invoice_grant_and_provision_once(
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
    first_payload["data"]["object"]["parent"]["subscription_details"][
        "metadata"
    ] = {
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
    await engine.dispose()

    assert first_response.status_code == second_response.status_code == 202
    assert len(ledgers) == 1
    assert ledgers[0].event_type == "subscription_activated"
    assert ledgers[0].source_id == first_payload["data"]["object"]["id"]
    assert [job[0] for job in pool.enqueued_jobs] == ["outbox_delivery_job"]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("event_type", "expected_status", "expected_outbox", "expected_ledger"),
    [
        ("customer.subscription.created", "active", 0, 0),
        ("customer.subscription.updated", "past_due", 1, 0),
        ("customer.subscription.deleted", "canceled", 1, 0),
        ("invoice.paid", "active", 1, 1),
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
    expected_jobs = (
        [("outbox_delivery_job", {})]
        if expected_outbox and event_type != "invoice.paid"
        else []
    )
    assert pool.enqueued_jobs == expected_jobs

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
        expected_topic = (
            "phone.provision" if event_type == "invoice.paid" else "phone.disable"
        )
        assert intent.topic == expected_topic
        assert intent.aggregate_type == "user"
        assert intent.aggregate_id == subscription.user_id
        if event_type == "invoice.paid":
            assert intent.idempotency_key == "stripe:invoice:in_123:phone.provision"
        else:
            assert intent.idempotency_key == f"stripe:{event_type}:{event_id}"


@pytest.mark.anyio
async def test_invoice_outbox_commit_survives_redis_wakeup_failure(
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
        session.add(User(clerk_user_id="user_123", email="redis-down@example.com"))
        await session.commit()
    await engine.dispose()

    payload = deepcopy(stripe_invoice_paid_payload)
    payload["data"]["object"]["lines"]["data"][0]["price"] = {
        "lookup_key": "starter"
    }
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
        assert event is not None
        assert event.status == "pending"
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
    newer.update(id="evt_update_newer", created=300, type="customer.subscription.updated")
    newer["data"]["object"].update(created=10, status="past_due")
    older = deepcopy(newer)
    older.update(id="evt_update_older", created=200)
    older["data"]["object"]["status"] = "active"

    assert (await _post_stripe_event(async_client, signed_stripe_headers_factory, newer)).status_code == 202
    assert (await _post_stripe_event(async_client, signed_stripe_headers_factory, older)).status_code == 202

    engine = create_async_engine(client_database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        subscription = await session.scalar(select(Subscription))
        outbox_events = list((await session.execute(select(OutboxEvent))).scalars())
    await engine.dispose()

    assert subscription is not None
    assert subscription.status == "past_due"
    assert subscription.last_stripe_event_created_at is not None
    assert subscription.last_stripe_event_created_at.replace(tzinfo=UTC) == datetime.fromtimestamp(300, UTC)
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
    deleted.update(id="evt_delete_newer", created=300, type="customer.subscription.deleted")
    deleted["data"]["object"]["status"] = "canceled"
    invoice = deepcopy(stripe_invoice_paid_payload)
    invoice.update(id="evt_invoice_older_than_delete", created=200)

    assert (await _post_stripe_event(async_client, signed_stripe_headers_factory, created)).status_code == 202
    assert (await _post_stripe_event(async_client, signed_stripe_headers_factory, deleted)).status_code == 202
    assert (await _post_stripe_event(async_client, signed_stripe_headers_factory, invoice)).status_code == 202

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
async def test_old_subscription_event_cannot_replace_new_resubscription(
    async_client,
    client_database_url,
    signed_stripe_headers_factory,
    stripe_subscription_created_payload,
) -> None:
    engine = create_async_engine(client_database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        session.add(User(clerk_user_id="user_123", email="resubscribe-order@example.com"))
        await session.commit()
    await engine.dispose()

    old_created = deepcopy(stripe_subscription_created_payload)
    old_created.update(id="evt_old_created", created=100)
    old_created["data"]["object"].update(id="sub_old", created=10)
    old_deleted = deepcopy(old_created)
    old_deleted.update(id="evt_old_deleted", created=150, type="customer.subscription.deleted")
    old_deleted["data"]["object"]["status"] = "canceled"
    new_created = deepcopy(stripe_subscription_created_payload)
    new_created.update(id="evt_new_created", created=200)
    new_created["data"]["object"].update(id="sub_new", created=20)
    old_late = deepcopy(old_created)
    old_late.update(id="evt_old_late", created=300, type="customer.subscription.updated")
    old_late["data"]["object"]["status"] = "past_due"

    for payload in (old_created, old_deleted, new_created, old_late):
        assert (await _post_stripe_event(async_client, signed_stripe_headers_factory, payload)).status_code == 202

    engine = create_async_engine(client_database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        subscription = await session.scalar(select(Subscription))
        outbox_events = list((await session.execute(select(OutboxEvent))).scalars())
    await engine.dispose()

    assert subscription is not None
    assert subscription.stripe_subscription_id == "sub_new"
    assert subscription.status == "active"
    assert subscription.stripe_subscription_created_at is not None
    assert subscription.stripe_subscription_created_at.replace(tzinfo=UTC) == datetime.fromtimestamp(20, UTC)
    assert len(outbox_events) == 1


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
    deleted.update(id="evt_equal_deleted", created=200, type="customer.subscription.deleted")
    deleted["data"]["object"]["status"] = "canceled"
    routing_update = deepcopy(created)
    routing_update.update(id="evt_equal_routing", created=200, type="customer.subscription.updated")

    for payload in (created, deleted, routing_update):
        assert (await _post_stripe_event(async_client, signed_stripe_headers_factory, payload)).status_code == 202

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
        session.add(User(clerk_user_id="user_123", email="invoice-conflict@example.com"))
        await session.commit()
    await engine.dispose()

    current = deepcopy(stripe_subscription_created_payload)
    current.update(id="evt_current_subscription", created=300)
    current["data"]["object"].update(id="sub_new", created=250)
    assert (await _post_stripe_event(async_client, signed_stripe_headers_factory, current)).status_code == 202

    conflicting_invoice = deepcopy(stripe_invoice_paid_payload)
    conflicting_invoice.update(id="evt_newer_mismatched_invoice", created=400)
    conflicting_invoice["data"]["object"]["parent"]["subscription_details"].update(
        subscription="sub_unknown",
        metadata={"clerk_user_id": "user_123", "plan_tier": "starter"},
    )
    conflicting_invoice["data"]["object"]["lines"]["data"][0]["parent"]["subscription_item_details"][
        "subscription"
    ] = "sub_unknown"

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
async def test_equal_generation_distinct_subscription_is_retryable_and_rolls_back(
    async_client,
    client_database_url,
    signed_stripe_headers_factory,
    stripe_subscription_created_payload,
) -> None:
    engine = create_async_engine(client_database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        session.add(User(clerk_user_id="user_123", email="equal-generation@example.com"))
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

    assert response.status_code == 503
    engine = create_async_engine(client_database_url, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        subscription = await session.scalar(select(Subscription))
        conflict_event = await session.scalar(
            select(WebhookEvent).where(
                WebhookEvent.external_event_id == "evt_equal_generation_ambiguous"
            )
        )
    await engine.dispose()

    assert subscription is not None
    assert subscription.stripe_subscription_id == "sub_equal_current"
    assert subscription.status == "canceled"
    assert conflict_event is None


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
    assert subscription.last_stripe_event_created_at is None
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
        user = User(clerk_user_id="user_123", email="legacy-invoice-failure@example.com")
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
    assert subscription.last_stripe_event_created_at is None
    assert len(outbox_events) == 1
