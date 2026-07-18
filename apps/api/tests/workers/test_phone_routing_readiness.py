from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.agent_config import AgentConfig
from app.models.phone_number import PhoneNumber
from app.models.subscription import Subscription
from app.models.usage_ledger import UsageLedger


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
    from app.workers.jobs.outbox_topics import deliver_phone_routing

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
    await db_session.commit()
    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    await deliver_phone_routing(
        {"session_factory": session_factory},
        SimpleNamespace(payload={"user_id": str(user_id)}),
    )

    db_session.expire_all()
    phone_number = await db_session.scalar(
        select(PhoneNumber).where(PhoneNumber.user_id == user_id)
    )
    assert phone_number is not None
    assert phone_number.provider_number_id == provider_number_id
    assert phone_number.provider_connection_name == "app-active"
    assert phone_number.is_active is True
