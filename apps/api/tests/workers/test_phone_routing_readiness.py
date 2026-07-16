from datetime import UTC, datetime

import pytest

from app.models.agent_config import AgentConfig
from app.models.phone_number import PhoneNumber
from app.models.subscription import Subscription
from app.models.usage_ledger import UsageLedger


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
