from dataclasses import replace

import pytest

from app.services.activation_policy import (
    ActivationFacts,
    ActivationPolicy,
    ActivationStage,
)


def ready_facts() -> ActivationFacts:
    return ActivationFacts(
        profile_confirmed=True,
        subscription_eligible=True,
        provisioning_consented=True,
        provisioning_status="succeeded",
        number_provisioned=True,
        verification_window_open=False,
        forwarding_verified=True,
        go_live_pending=False,
        go_live_approved=True,
        runtime_ready=True,
    )


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"profile_confirmed": False}, ActivationStage.PROFILE_REQUIRED),
        ({"subscription_eligible": False}, ActivationStage.PAYMENT_REQUIRED),
        (
            {
                "provisioning_consented": False,
                "provisioning_status": "running",
                "number_provisioned": False,
            },
            ActivationStage.PROVISIONING_CONSENT_REQUIRED,
        ),
        (
            {"provisioning_status": "running", "number_provisioned": False},
            ActivationStage.PROVISIONING,
        ),
        ({"provisioning_status": "failed"}, ActivationStage.PROVISIONING_FAILED),
        (
            {"number_provisioned": True, "forwarding_verified": False},
            ActivationStage.FORWARDING_REQUIRED,
        ),
        (
            {"verification_window_open": True},
            ActivationStage.VERIFICATION_WINDOW_OPEN,
        ),
        (
            {"forwarding_verified": True, "go_live_approved": False},
            ActivationStage.READY_TO_ACTIVATE,
        ),
        ({"go_live_pending": True}, ActivationStage.ACTIVATING),
        (
            {"go_live_approved": True, "runtime_ready": False},
            ActivationStage.RUNTIME_PAUSED,
        ),
        (
            {"go_live_approved": True, "runtime_ready": True},
            ActivationStage.ACTIVE,
        ),
    ],
)
def test_activation_stage_precedence(
    overrides: dict[str, object],
    expected: ActivationStage,
) -> None:
    facts = replace(ready_facts(), **overrides)

    assert ActivationPolicy.evaluate(facts).stage is expected


def test_activation_decision_exposes_stable_actions_blockers_and_milestones() -> None:
    decision = ActivationPolicy.evaluate(
        replace(
            ready_facts(),
            forwarding_verified=False,
            go_live_approved=False,
            runtime_ready=False,
        )
    )

    assert decision.stage is ActivationStage.FORWARDING_REQUIRED
    assert decision.completed_milestones == (
        "profile_confirmed",
        "payment_eligible",
        "provisioning_consented",
        "number_provisioned",
    )
    assert decision.next_action == "configure_forwarding"
    assert decision.blockers == ("forwarding_not_verified",)


def test_completed_legacy_number_advances_without_historical_consent() -> None:
    decision = ActivationPolicy.evaluate(
        replace(
            ready_facts(),
            provisioning_consented=False,
            forwarding_verified=False,
            go_live_approved=False,
        )
    )

    assert decision.stage is ActivationStage.FORWARDING_REQUIRED
    assert decision.next_action == "configure_forwarding"
    assert decision.blockers == ("forwarding_not_verified",)
    assert "number_provisioned" in decision.completed_milestones
    assert "provisioning_consented" not in decision.completed_milestones


def test_missing_assigned_number_remains_in_provisioning() -> None:
    decision = ActivationPolicy.evaluate(
        replace(
            ready_facts(),
            provisioning_status="running",
            number_provisioned=False,
        )
    )

    assert decision.stage is ActivationStage.PROVISIONING
    assert decision.blockers == ("number_not_ready",)


def test_succeeded_provisioning_with_invalid_assignment_is_terminal() -> None:
    decision = ActivationPolicy.evaluate(
        replace(
            ready_facts(),
            provisioning_status="succeeded",
            number_provisioned=False,
        )
    )

    assert decision.stage is ActivationStage.PROVISIONING_FAILED
    assert decision.next_action is None
    assert decision.blockers == ("number_assignment_inconsistent",)
