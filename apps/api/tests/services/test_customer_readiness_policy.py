from datetime import UTC, datetime

import pytest

from app.schemas.agent_content import (
    AGENT_NAME_MAX_LENGTH,
    KNOWLEDGE_BASE_MAX_LENGTH,
    OWNER_CONTEXT_MAX_LENGTH,
    SYSTEM_PROMPT_MAX_LENGTH,
)
from app.services.customer_readiness_policy import (
    CustomerReadinessPolicy,
    CustomerReadinessSnapshot,
    CustomerReadinessStage,
    ReadinessBlocker,
)


NOW = datetime(2026, 7, 16, 12, tzinfo=UTC)


def ready_snapshot(**overrides: object) -> CustomerReadinessSnapshot:
    values: dict[str, object] = {
        "user_status": "active",
        "plan_tier": "starter",
        "subscription_status": "active",
        "current_period_start": datetime(2026, 7, 1, tzinfo=UTC),
        "current_period_end": datetime(2026, 8, 1, tzinfo=UTC),
        "balance": 30,
        "provisioning_status": "succeeded",
        "phone_present": True,
        "phone_provider_id_present": True,
        "phone_active": True,
        "phone_connection_name": "app-active",
        "agent_present": True,
        "agent_enabled": True,
        "agent_name": "Ava",
        "owner_context": "Sam runs a plumbing business in Lyon.",
        "system_prompt": "Keep answers concise.",
        "knowledge_base": "Open weekdays from nine to five.",
    }
    values.update(overrides)
    return CustomerReadinessSnapshot(**values)  # type: ignore[arg-type]


def evaluate(**overrides: object):
    return CustomerReadinessPolicy.evaluate(
        ready_snapshot(**overrides),
        now=NOW,
    )


def test_live_snapshot_can_activate_route_and_dispatch() -> None:
    result = evaluate()

    assert result.stage is CustomerReadinessStage.LIVE
    assert result.can_provision_number is True
    assert result.can_activate is True
    assert result.should_enable_phone is True
    assert result.can_route is True
    assert result.can_dispatch(called_number_matches=True) is True
    assert result.can_dispatch(called_number_matches=False) is False
    assert result.blockers == ()
    assert result.warnings == ()
    assert result.evaluated_at == NOW
    assert result.policy_version == "runtime-v1"


def test_period_end_is_exclusive() -> None:
    result = evaluate(current_period_end=NOW)

    assert ReadinessBlocker.SUBSCRIPTION_PERIOD_INACTIVE in result.blockers
    assert result.can_provision_number is False
    assert result.can_activate is False
    assert result.can_route is False
    assert result.stage is CustomerReadinessStage.SUSPENDED


def test_naive_subscription_period_is_treated_as_utc() -> None:
    result = evaluate(
        current_period_start=datetime(2026, 7, 1),
        current_period_end=datetime(2026, 8, 1),
    )

    assert result.can_route is True
    assert result.evaluated_at.tzinfo is UTC


@pytest.mark.parametrize(
    ("overrides", "expected_blocker"),
    [
        ({"user_status": None}, ReadinessBlocker.USER_INACTIVE),
        (
            {"subscription_status": None},
            ReadinessBlocker.SUBSCRIPTION_MISSING,
        ),
        ({"plan_tier": "standard"}, ReadinessBlocker.PLAN_UNSUPPORTED),
        (
            {"subscription_status": "past_due"},
            ReadinessBlocker.SUBSCRIPTION_STATUS_INELIGIBLE,
        ),
        (
            {"current_period_start": None},
            ReadinessBlocker.SUBSCRIPTION_PERIOD_MISSING,
        ),
        (
            {"current_period_end": datetime(2026, 7, 15, tzinfo=UTC)},
            ReadinessBlocker.SUBSCRIPTION_PERIOD_INACTIVE,
        ),
        ({"balance": 0}, ReadinessBlocker.MINUTES_EXHAUSTED),
        ({"phone_present": False}, ReadinessBlocker.PHONE_MISSING),
        (
            {"phone_provider_id_present": False},
            ReadinessBlocker.PHONE_PROVIDER_ID_MISSING,
        ),
        ({"agent_present": False}, ReadinessBlocker.AGENT_CONFIG_MISSING),
        (
            {"agent_name": " assistant "},
            ReadinessBlocker.AGENT_SETUP_INCOMPLETE,
        ),
        (
            {"owner_context": "   "},
            ReadinessBlocker.AGENT_SETUP_INCOMPLETE,
        ),
        (
            {"system_prompt": " ", "knowledge_base": ""},
            ReadinessBlocker.AGENT_SETUP_INCOMPLETE,
        ),
        (
            {"agent_name": "a" * (AGENT_NAME_MAX_LENGTH + 1)},
            ReadinessBlocker.AGENT_CONTENT_INVALID,
        ),
        (
            {"owner_context": "o" * (OWNER_CONTEXT_MAX_LENGTH + 1)},
            ReadinessBlocker.AGENT_CONTENT_INVALID,
        ),
        (
            {"system_prompt": "s" * (SYSTEM_PROMPT_MAX_LENGTH + 1)},
            ReadinessBlocker.AGENT_CONTENT_INVALID,
        ),
        (
            {"knowledge_base": "k" * (KNOWLEDGE_BASE_MAX_LENGTH + 1)},
            ReadinessBlocker.AGENT_CONTENT_INVALID,
        ),
        ({"agent_enabled": False}, ReadinessBlocker.AGENT_DISABLED),
        ({"phone_active": False}, ReadinessBlocker.PHONE_INACTIVE),
        (
            {"phone_connection_name": "app-disabled"},
            ReadinessBlocker.PHONE_PROJECTION_INACTIVE,
        ),
    ],
)
def test_policy_reports_each_runtime_blocker(
    overrides: dict[str, object],
    expected_blocker: ReadinessBlocker,
) -> None:
    result = evaluate(**overrides)

    assert expected_blocker in result.blockers
    assert result.can_route is False


@pytest.mark.parametrize(
    "subscription_status",
    ["active", "trialing"],
)
def test_active_and_trialing_are_the_only_eligible_statuses(
    subscription_status: str,
) -> None:
    assert evaluate(subscription_status=subscription_status).can_route is True


def test_missing_period_reports_one_missing_period_blocker() -> None:
    result = evaluate(current_period_start=None, current_period_end=None)

    assert result.blockers.count(ReadinessBlocker.SUBSCRIPTION_PERIOD_MISSING) == 1
    assert ReadinessBlocker.SUBSCRIPTION_PERIOD_INACTIVE not in result.blockers


def test_maximum_content_lengths_remain_eligible() -> None:
    result = evaluate(
        agent_name="a" * AGENT_NAME_MAX_LENGTH,
        owner_context="o" * OWNER_CONTEXT_MAX_LENGTH,
        system_prompt="s" * SYSTEM_PROMPT_MAX_LENGTH,
        knowledge_base="k" * KNOWLEDGE_BASE_MAX_LENGTH,
    )

    assert ReadinessBlocker.AGENT_CONTENT_INVALID not in result.blockers
    assert result.can_route is True


def test_disabled_agent_is_ready_to_activate_but_not_routable() -> None:
    result = evaluate(agent_enabled=False)

    assert result.stage is CustomerReadinessStage.READY
    assert result.can_provision_number is True
    assert result.can_activate is True
    assert result.should_enable_phone is False
    assert result.can_route is False
    assert result.blockers == (ReadinessBlocker.AGENT_DISABLED,)


@pytest.mark.parametrize(
    "overrides",
    [
        {"phone_active": False},
        {"phone_connection_name": "app-disabled"},
    ],
)
def test_enabled_agent_waiting_for_provider_projection_is_pending(
    overrides: dict[str, object],
) -> None:
    result = evaluate(**overrides)

    assert result.stage is CustomerReadinessStage.ROUTING_PENDING
    assert result.can_activate is True
    assert result.should_enable_phone is True
    assert result.can_route is False


@pytest.mark.parametrize(
    ("overrides", "expected_stage"),
    [
        (
            {"subscription_status": None},
            CustomerReadinessStage.SUBSCRIPTION_REQUIRED,
        ),
        ({"balance": 0}, CustomerReadinessStage.SUSPENDED),
        (
            {
                "phone_present": False,
                "phone_provider_id_present": False,
                "phone_active": False,
                "phone_connection_name": None,
                "provisioning_status": "running",
            },
            CustomerReadinessStage.NUMBER_PROVISIONING,
        ),
        (
            {
                "phone_present": False,
                "phone_provider_id_present": False,
                "phone_active": False,
                "phone_connection_name": None,
                "provisioning_status": "failed",
            },
            CustomerReadinessStage.NUMBER_PROVISIONING_FAILED,
        ),
        (
            {"phone_provider_id_present": False},
            CustomerReadinessStage.NUMBER_PROVISIONING_FAILED,
        ),
        (
            {"agent_name": "Assistant"},
            CustomerReadinessStage.RECEPTIONIST_SETUP_REQUIRED,
        ),
    ],
)
def test_stage_precedence(
    overrides: dict[str, object],
    expected_stage: CustomerReadinessStage,
) -> None:
    assert evaluate(**overrides).stage is expected_stage


def test_blockers_are_returned_in_public_enum_order() -> None:
    result = evaluate(
        user_status="disabled",
        plan_tier="standard",
        subscription_status="past_due",
        current_period_start=None,
        balance=0,
        phone_present=False,
        phone_provider_id_present=False,
        phone_active=False,
        phone_connection_name=None,
        agent_present=False,
        agent_enabled=False,
    )

    assert result.blockers == tuple(
        blocker for blocker in ReadinessBlocker if blocker in result.blockers
    )
