from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.agent_config import AgentConfig
from app.models.phone_number import PhoneNumber
from app.models.phone_number_provisioning import PhoneNumberProvisioning
from app.models.subscription import Subscription
from app.models.usage_ledger import UsageLedger
from app.services.account_access_policy import AccountStateBlockedError
from app.services.outbox_service import OutboxService
from app.workers.jobs.outbox_delivery import OutboxDeliveryError


@pytest.fixture(autouse=True)
def _activation_flow_defaults_off_for_legacy_routing_tests(
    monkeypatch: pytest.MonkeyPatch,
):
    from app.core.config import get_settings

    monkeypatch.setenv("ACTIVATION_FLOW_ENABLED", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("period_start", "period_end", "balance"),
    [
        (
            datetime(2098, 1, 1, tzinfo=UTC),
            datetime(2099, 1, 1, tzinfo=UTC),
            60,
        ),
        (
            datetime(2020, 1, 1, tzinfo=UTC),
            datetime(2021, 1, 1, tzinfo=UTC),
            60,
        ),
        (
            datetime(2026, 1, 1, tzinfo=UTC),
            datetime(2099, 1, 1, tzinfo=UTC),
            0,
        ),
    ],
)
async def test_routing_stays_disabled_without_current_financial_access(
    db_session,
    active_user,
    period_start: datetime,
    period_end: datetime,
    balance: int,
) -> None:
    from app.workers.jobs.outbox_topics import _routing_snapshot

    db_session.add(
        Subscription(
            user_id=active_user.id,
            stripe_customer_id="cus_future_period",
            stripe_subscription_id="sub_future_period",
            plan_tier="starter",
            status="active",
            allocated_minutes=60,
            current_period_start=period_start,
            current_period_end=period_end,
        )
    )
    db_session.add(
        UsageLedger(
            user_id=active_user.id,
            event_type="subscription_activated",
            minutes_delta=balance,
            balance_after=balance,
        )
    )
    db_session.add(
        AgentConfig(
            user_id=active_user.id,
            agent_name="Presvo Front Desk",
            owner_context="Dental office reception",
            system_prompt="Handle inbound calls professionally.",
            knowledge_base="Open weekdays.",
            pipeline_mode="stt_llm_tts",
            is_enabled=True,
        )
    )
    db_session.add(
        PhoneNumber(
            user_id=active_user.id,
            e164="+35315551234",
            country_code="IE",
            provider="telnyx",
            provider_number_id="pn_future_period",
            provider_connection_name="app-disabled",
            is_active=False,
        )
    )
    await db_session.commit()

    snapshot = await _routing_snapshot(db_session, active_user.id)

    assert snapshot is not None
    assert snapshot.should_enable is False


@pytest.mark.anyio
async def test_routing_uses_local_factory_without_injected_provider(
    db_session,
    active_user,
) -> None:
    from app.services.outbox_service import OutboxService
    from app.workers.jobs.outbox_delivery import outbox_delivery_job

    user_id = active_user.id
    now = datetime.now(UTC)
    provider_number_id = "fake-0123456789abcdef"
    db_session.add_all(
        [
            Subscription(
                user_id=user_id,
                stripe_customer_id="local_customer_routing",
                stripe_subscription_id="local_subscription_routing",
                plan_tier="starter",
                status="active",
                allocated_minutes=60,
                current_period_start=now - timedelta(days=1),
                current_period_end=now + timedelta(days=29),
            ),
            UsageLedger(
                user_id=user_id,
                event_type="subscription_activated",
                source_id="local-starter:routing",
                minutes_delta=60,
                balance_after=60,
            ),
            AgentConfig(
                user_id=user_id,
                agent_name="Presvo Front Desk",
                owner_context="Dental office reception",
                system_prompt="Handle inbound calls professionally.",
                knowledge_base="Open weekdays.",
                pipeline_mode="stt_llm_tts",
                is_enabled=True,
            ),
            PhoneNumber(
                user_id=user_id,
                e164="+33912345678",
                country_code="FR",
                provider="telnyx",
                provider_number_id=provider_number_id,
                provider_connection_name="app-disabled",
                is_active=False,
            ),
        ]
    )
    await OutboxService(db_session).add(
        topic="phone.enable",
        aggregate_type="user",
        aggregate_id=user_id,
        idempotency_key=f"test:phone.enable:{user_id}",
        payload={"user_id": str(user_id)},
    )
    await db_session.commit()
    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    result = await outbox_delivery_job({"session_factory": session_factory})

    db_session.expire_all()
    phone_number = await db_session.scalar(
        select(PhoneNumber).where(PhoneNumber.user_id == user_id)
    )
    assert phone_number is not None
    assert phone_number.provider_number_id == provider_number_id
    assert phone_number.provider_connection_name == "app-active"
    assert phone_number.is_active is True
    assert result == {"claimed": 1, "delivered": 1, "retried": 0, "failed": 0}


@pytest.mark.anyio
async def test_claimed_phone_provision_rechecks_account_before_provider_io(
    db_session,
    active_user,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.workers.jobs import phone_provisioning

    user_id = active_user.id
    operation_key = f"phone-provision-race:{user_id}"
    db_session.add(
        PhoneNumberProvisioning(
            user_id=user_id,
            target_country_code="FR",
            status="queued",
            provider_operation_key=operation_key,
        )
    )
    await db_session.commit()
    provider_calls: list[str] = []

    class Provider:
        async def provision_number(self, **_kwargs):
            provider_calls.append("provision")
            raise AssertionError("provider must not be called")

    @asynccontextmanager
    async def deactivate_before_provider(_session_factory, _operation_key):
        active_user.status = "deactivating"
        await db_session.commit()
        yield

    monkeypatch.setattr(
        phone_provisioning,
        "_provider_operation_lock",
        deactivate_before_provider,
    )
    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    with pytest.raises(AccountStateBlockedError) as raised:
        await phone_provisioning.phone_provisioning_job(
            {
                "session_factory": session_factory,
                "telephony_provider": Provider(),
            },
            {"user_id": str(user_id)},
            provider_operation_key=operation_key,
        )

    assert raised.value.code == "account_deactivating"
    assert provider_calls == []


@pytest.mark.anyio
async def test_claimed_phone_enable_rechecks_account_before_provider_io(
    db_session,
    active_user,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.workers.jobs import outbox_topics

    user_id = active_user.id
    now = datetime.now(UTC)
    db_session.add_all(
        [
            Subscription(
                user_id=user_id,
                stripe_customer_id="routing-race-customer",
                stripe_subscription_id="routing-race-subscription",
                plan_tier="starter",
                status="active",
                allocated_minutes=60,
                current_period_start=now - timedelta(days=1),
                current_period_end=now + timedelta(days=1),
            ),
            UsageLedger(
                user_id=user_id,
                event_type="subscription_activated",
                source_id="routing-race-ledger",
                minutes_delta=60,
                balance_after=60,
            ),
            AgentConfig(
                user_id=user_id,
                agent_name="Presvo Front Desk",
                owner_context="Dental office reception",
                system_prompt="Handle inbound calls professionally.",
                knowledge_base="Open weekdays.",
                pipeline_mode="stt_llm_tts",
                is_enabled=True,
            ),
            PhoneNumber(
                user_id=user_id,
                e164="+33912340000",
                country_code="FR",
                provider="telnyx",
                provider_number_id="routing-race-number",
                provider_connection_name="app-disabled",
                is_active=False,
            ),
        ]
    )
    event = await OutboxService(db_session).add(
        topic="phone.enable",
        aggregate_type="user",
        aggregate_id=user_id,
        idempotency_key=f"routing-race:{user_id}",
        payload={"user_id": str(user_id)},
    )
    event.status = "processing"
    event.attempt_count = 1
    await db_session.commit()
    provider_calls: list[str] = []

    class Provider:
        async def enable_number(self, *, provider_number_id: str) -> str:
            provider_calls.append(f"enable:{provider_number_id}")
            return "app-active"

        async def disable_number(self, *, provider_number_id: str) -> str:
            provider_calls.append(f"disable:{provider_number_id}")
            return "app-disabled"

    real_set_routing_target = outbox_topics._set_routing_target

    async def set_target_then_deactivate(*args, **kwargs):
        await real_set_routing_target(*args, **kwargs)
        await db_session.refresh(active_user)
        active_user.status = "deactivating"
        await db_session.commit()

    monkeypatch.setattr(
        outbox_topics,
        "_set_routing_target",
        set_target_then_deactivate,
    )
    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    with pytest.raises(OutboxDeliveryError) as raised:
        await outbox_topics.deliver_phone_routing(
            {
                "session_factory": session_factory,
                "telephony_provider": Provider(),
            },
            event,
        )

    assert raised.value.error_code == "dispatch_ineligible"
    assert raised.value.retryable is False
    assert provider_calls == []
