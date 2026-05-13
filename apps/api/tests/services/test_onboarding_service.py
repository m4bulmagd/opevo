from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.models.agent_config import AgentConfig
from app.models.phone_number import PhoneNumber
from app.models.phone_number_provisioning import PhoneNumberProvisioning
from app.models.subscription import Subscription
from app.models.usage_ledger import UsageLedger


@pytest.mark.anyio
async def test_get_status_returns_not_subscribed_defaults(db_session, active_user) -> None:
    from app.services.onboarding_service import OnboardingService

    status = await OnboardingService(db_session).get_status(active_user.id)

    assert status.subscription_status is None
    assert status.plan_tier is None
    assert status.minutes_remaining == 0
    assert status.phone_number is None
    assert status.phone_number_status == "missing"
    assert status.routing_enabled is False
    assert status.agent_setup_complete is False
    assert status.overall_status == "not_subscribed"
    assert status.can_retry_provisioning is False


@pytest.mark.anyio
async def test_get_status_returns_provisioning_failed_with_retry(db_session, active_user) -> None:
    from app.services.onboarding_service import OnboardingService

    db_session.add(
        Subscription(
            user_id=active_user.id,
            stripe_customer_id="cus_123",
            stripe_subscription_id="sub_123",
            plan_tier="starter",
            status="active",
            allocated_minutes=60,
            current_period_start=datetime(2026, 4, 1, tzinfo=UTC),
            current_period_end=datetime(2026, 5, 1, tzinfo=UTC),
        )
    )
    db_session.add(
        PhoneNumberProvisioning(
            user_id=active_user.id,
            target_country_code="FR",
            status="failed",
            attempt_count=1,
            can_retry=True,
            last_error_reason="no_affordable_number",
            last_error_payload={"contact_support": True},
        )
    )
    await db_session.commit()

    status = await OnboardingService(db_session).get_status(active_user.id)

    assert status.phone_number_status == "failed"
    assert status.can_retry_provisioning is True
    assert status.overall_status == "provisioning_failed"


@pytest.mark.anyio
async def test_get_status_returns_setup_required_when_number_ready_but_setup_incomplete(
    db_session, active_user
) -> None:
    from app.services.onboarding_service import OnboardingService

    phone_number_id = uuid4()
    db_session.add(
        Subscription(
            user_id=active_user.id,
            stripe_customer_id="cus_123",
            stripe_subscription_id="sub_123",
            plan_tier="starter",
            status="active",
            allocated_minutes=60,
            current_period_start=datetime(2026, 4, 1, tzinfo=UTC),
            current_period_end=datetime(2026, 5, 1, tzinfo=UTC),
        )
    )
    db_session.add(
        PhoneNumber(
            id=phone_number_id,
            user_id=active_user.id,
            e164="+33123456789",
            country_code="FR",
            provider="telnyx",
            provider_number_id="pn_123",
            provider_connection_name="app-disabled",
            is_active=False,
        )
    )
    db_session.add(
        PhoneNumberProvisioning(
            user_id=active_user.id,
            phone_number_id=phone_number_id,
            target_country_code="FR",
            status="succeeded",
            attempt_count=1,
            can_retry=False,
        )
    )
    db_session.add(
        AgentConfig(
            user_id=active_user.id,
            agent_name="Assistant",
            owner_context="",
            system_prompt="",
            knowledge_base="",
            pipeline_mode="stt_llm_tts",
            is_enabled=False,
        )
    )
    await db_session.commit()

    status = await OnboardingService(db_session).get_status(active_user.id)

    assert status.phone_number == "+33123456789"
    assert status.phone_number_status == "ready"
    assert status.agent_setup_complete is False
    assert status.overall_status == "setup_required"


@pytest.mark.anyio
async def test_get_status_returns_ready_to_enable_when_setup_complete_and_routing_off(
    db_session, active_user
) -> None:
    from app.services.onboarding_service import OnboardingService

    phone_number_id = uuid4()
    db_session.add(
        Subscription(
            user_id=active_user.id,
            stripe_customer_id="cus_123",
            stripe_subscription_id="sub_123",
            plan_tier="starter",
            status="active",
            allocated_minutes=60,
            current_period_start=datetime(2026, 4, 1, tzinfo=UTC),
            current_period_end=datetime(2026, 5, 1, tzinfo=UTC),
        )
    )
    db_session.add(
        UsageLedger(
            user_id=active_user.id,
            event_type="subscription_activated",
            minutes_delta=60,
            balance_after=60,
        )
    )
    db_session.add(
        PhoneNumber(
            id=phone_number_id,
            user_id=active_user.id,
            e164="+33123456789",
            country_code="FR",
            provider="telnyx",
            provider_number_id="pn_123",
            provider_connection_name="app-disabled",
            is_active=False,
        )
    )
    db_session.add(
        PhoneNumberProvisioning(
            user_id=active_user.id,
            phone_number_id=phone_number_id,
            target_country_code="FR",
            status="succeeded",
            attempt_count=1,
            can_retry=False,
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
            is_enabled=False,
        )
    )
    await db_session.commit()

    status = await OnboardingService(db_session).get_status(active_user.id)

    assert status.minutes_remaining == 60
    assert status.routing_enabled is False
    assert status.agent_setup_complete is True
    assert status.overall_status == "ready_to_enable"


@pytest.mark.anyio
async def test_get_status_returns_live_when_routing_enabled(db_session, active_user) -> None:
    from app.services.onboarding_service import OnboardingService

    phone_number_id = uuid4()
    db_session.add(
        Subscription(
            user_id=active_user.id,
            stripe_customer_id="cus_123",
            stripe_subscription_id="sub_123",
            plan_tier="starter",
            status="active",
            allocated_minutes=60,
            current_period_start=datetime(2026, 4, 1, tzinfo=UTC),
            current_period_end=datetime(2026, 5, 1, tzinfo=UTC),
        )
    )
    db_session.add(
        PhoneNumber(
            id=phone_number_id,
            user_id=active_user.id,
            e164="+33123456789",
            country_code="FR",
            provider="telnyx",
            provider_number_id="pn_123",
            provider_connection_name="app-active",
            is_active=True,
        )
    )
    db_session.add(
        PhoneNumberProvisioning(
            user_id=active_user.id,
            phone_number_id=phone_number_id,
            target_country_code="FR",
            status="succeeded",
            attempt_count=1,
            can_retry=False,
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
    await db_session.commit()

    status = await OnboardingService(db_session).get_status(active_user.id)

    assert status.routing_enabled is True
    assert status.overall_status == "live"
