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
        phone_ready=True,
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
            {"provisioning_consented": False},
            ActivationStage.PROVISIONING_CONSENT_REQUIRED,
        ),
        ({"provisioning_status": "running"}, ActivationStage.PROVISIONING),
        ({"provisioning_status": "failed"}, ActivationStage.PROVISIONING_FAILED),
        (
            {"phone_ready": True, "forwarding_verified": False},
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


def test_missing_assigned_number_remains_in_provisioning() -> None:
    decision = ActivationPolicy.evaluate(replace(ready_facts(), phone_ready=False))

    assert decision.stage is ActivationStage.PROVISIONING
    assert decision.blockers == ("number_not_ready",)
