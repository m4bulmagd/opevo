import inspect
from datetime import UTC, datetime, timedelta

import pytest

from app.core.config import get_settings
from app.models.agent_config import AgentConfig
from app.models.phone_number import PhoneNumber
from app.models.phone_number_provisioning import PhoneNumberProvisioning
from app.models.subscription import Subscription
from app.models.usage_ledger import UsageLedger

from app.services.account_lifecycle_service import AccountLifecycleService
from app.services.agent_config_service import AgentConfigService
from app.services.call_reconciliation_service import CallReconciliationService
from app.services.carrier_lookup_service import CarrierLookupService
from app.services.customer_readiness_service import CustomerReadinessService
from app.services.livekit_dispatch_service import LiveKitDispatchService
from app.services.onboarding_service import OnboardingService
from app.services.telephony_service import TelephonyService


@pytest.mark.parametrize(
    ("constructor", "dependency"),
    [
        (AgentConfigService, "activation_flow_enabled"),
        (CustomerReadinessService, "activation_flow_enabled"),
        (CallReconciliationService, "settings"),
        (CarrierLookupService, "provider"),
        (TelephonyService, "provider"),
        (LiveKitDispatchService, "activation_flow_enabled"),
        (OnboardingService, "activation_flow_enabled"),
        (AccountLifecycleService, "activation_flow_enabled"),
    ],
)
def test_domain_policy_dependency_is_required(constructor, dependency: str) -> None:
    parameter = inspect.signature(constructor).parameters[dependency]

    assert parameter.default is inspect.Parameter.empty


async def _seed_legacy_ready_customer(db_session, user) -> None:
    now = datetime.now(UTC)
    phone = PhoneNumber(
        user_id=user.id,
        e164="+33123456789",
        country_code="FR",
        provider="telnyx",
        provider_number_id="pn-explicit-policy",
        provider_connection_name="app-active",
        is_active=True,
    )
    db_session.add_all(
        [
            phone,
            Subscription(
                user_id=user.id,
                stripe_customer_id="cus-explicit-policy",
                stripe_subscription_id="sub-explicit-policy",
                plan_tier="starter",
                status="active",
                allocated_minutes=60,
                current_period_start=now - timedelta(days=1),
                current_period_end=now + timedelta(days=1),
            ),
            AgentConfig(
                user_id=user.id,
                agent_name="Ava",
                owner_context="Sam's plumbing business",
                system_prompt="Be concise.",
                knowledge_base="Open weekdays.",
                pipeline_mode="stt_llm_tts",
                is_enabled=True,
            ),
            UsageLedger(
                user_id=user.id,
                event_type="subscription_activated",
                source_id="explicit-policy",
                minutes_delta=60,
                balance_after=60,
            ),
        ]
    )
    await db_session.flush()
    db_session.add(
        PhoneNumberProvisioning(
            user_id=user.id,
            phone_number_id=phone.id,
            target_country_code="FR",
            status="succeeded",
            attempt_count=1,
            can_retry=False,
        )
    )
    await db_session.commit()


@pytest.mark.anyio
async def test_onboarding_nested_readiness_uses_explicit_activation_policy(
    db_session,
    active_user,
) -> None:
    assert get_settings().activation_flow_enabled is False
    await _seed_legacy_ready_customer(db_session, active_user)

    status = await OnboardingService(
        db_session,
        activation_flow_enabled=True,
    ).get_status(active_user.id)

    assert status.can_route is False
    assert "business_profile_incomplete" in status.blockers
    assert "go_live_not_approved" in status.blockers


@pytest.mark.anyio
async def test_account_lifecycle_nested_readiness_uses_explicit_activation_policy(
    db_session,
    active_user,
) -> None:
    assert get_settings().activation_flow_enabled is False
    await _seed_legacy_ready_customer(db_session, active_user)

    account = await AccountLifecycleService(
        db_session,
        activation_flow_enabled=True,
    ).get_account(active_user.id)

    assert account.serving is False
    assert account.blocker == "customer_not_ready"
