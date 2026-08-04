from dataclasses import dataclass
from enum import StrEnum


class ActivationStage(StrEnum):
    PROFILE_REQUIRED = "profile_required"
    PAYMENT_REQUIRED = "payment_required"
    PROVISIONING_CONSENT_REQUIRED = "provisioning_consent_required"
    PROVISIONING = "provisioning"
    PROVISIONING_FAILED = "provisioning_failed"
    FORWARDING_REQUIRED = "forwarding_required"
    VERIFICATION_WINDOW_OPEN = "verification_window_open"
    READY_TO_ACTIVATE = "ready_to_activate"
    ACTIVATING = "activating"
    RUNTIME_PAUSED = "runtime_paused"
    ACTIVE = "active"


@dataclass(frozen=True, slots=True)
class ActivationFacts:
    profile_confirmed: bool
    subscription_eligible: bool
    provisioning_consented: bool
    provisioning_status: str | None
    number_provisioned: bool
    verification_window_open: bool
    forwarding_verified: bool
    go_live_pending: bool
    go_live_approved: bool
    runtime_ready: bool
    runtime_blockers: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ActivationDecision:
    stage: ActivationStage
    completed_milestones: tuple[str, ...]
    next_action: str | None
    blockers: tuple[str, ...]


class ActivationPolicy:
    @classmethod
    def evaluate(cls, facts: ActivationFacts) -> ActivationDecision:
        completed_milestones = cls._completed_milestones(facts)

        if not facts.profile_confirmed:
            return ActivationDecision(
                stage=ActivationStage.PROFILE_REQUIRED,
                completed_milestones=completed_milestones,
                next_action="complete_profile",
                blockers=("profile_not_confirmed",),
            )
        if not facts.subscription_eligible:
            return ActivationDecision(
                stage=ActivationStage.PAYMENT_REQUIRED,
                completed_milestones=completed_milestones,
                next_action="start_checkout",
                blockers=("subscription_not_eligible",),
            )
        if not facts.provisioning_consented and not facts.number_provisioned:
            return ActivationDecision(
                stage=ActivationStage.PROVISIONING_CONSENT_REQUIRED,
                completed_milestones=completed_milestones,
                next_action="confirm_provisioning",
                blockers=("provisioning_consent_required",),
            )
        if facts.provisioning_status == "failed":
            return ActivationDecision(
                stage=ActivationStage.PROVISIONING_FAILED,
                completed_milestones=completed_milestones,
                next_action="retry_provisioning",
                blockers=("number_provisioning_failed",),
            )
        if (
            facts.provisioning_status == "succeeded"
            and not facts.number_provisioned
        ):
            return ActivationDecision(
                stage=ActivationStage.PROVISIONING_FAILED,
                completed_milestones=completed_milestones,
                next_action=None,
                blockers=("number_assignment_inconsistent",),
            )
        if not facts.number_provisioned:
            return ActivationDecision(
                stage=ActivationStage.PROVISIONING,
                completed_milestones=completed_milestones,
                next_action=None,
                blockers=("number_not_ready",),
            )
        if not facts.forwarding_verified and not facts.verification_window_open:
            return ActivationDecision(
                stage=ActivationStage.FORWARDING_REQUIRED,
                completed_milestones=completed_milestones,
                next_action="configure_forwarding",
                blockers=("forwarding_not_verified",),
            )
        if facts.verification_window_open:
            return ActivationDecision(
                stage=ActivationStage.VERIFICATION_WINDOW_OPEN,
                completed_milestones=completed_milestones,
                next_action="complete_forwarding_verification",
                blockers=(),
            )
        if facts.go_live_pending:
            return ActivationDecision(
                stage=ActivationStage.ACTIVATING,
                completed_milestones=completed_milestones,
                next_action=None,
                blockers=(),
            )
        if not facts.go_live_approved:
            return ActivationDecision(
                stage=ActivationStage.READY_TO_ACTIVATE,
                completed_milestones=completed_milestones,
                next_action="go_live",
                blockers=(),
            )
        if not facts.runtime_ready:
            return ActivationDecision(
                stage=ActivationStage.RUNTIME_PAUSED,
                completed_milestones=completed_milestones,
                next_action="resolve_runtime_blockers",
                blockers=facts.runtime_blockers or ("runtime_not_ready",),
            )
        return ActivationDecision(
            stage=ActivationStage.ACTIVE,
            completed_milestones=completed_milestones,
            next_action=None,
            blockers=(),
        )

    @staticmethod
    def _completed_milestones(facts: ActivationFacts) -> tuple[str, ...]:
        completed: list[str] = []
        if facts.profile_confirmed:
            completed.append("profile_confirmed")
        if facts.subscription_eligible:
            completed.append("payment_eligible")
        if facts.provisioning_consented:
            completed.append("provisioning_consented")
        if facts.number_provisioned:
            completed.append("number_provisioned")
        if facts.forwarding_verified:
            completed.append("forwarding_verified")
        if facts.go_live_approved:
            completed.append("go_live_approved")
        if facts.go_live_approved and not facts.go_live_pending:
            completed.append("activated")
        return tuple(completed)
