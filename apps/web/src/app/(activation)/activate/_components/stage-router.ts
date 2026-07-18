import type { ActivationSnapshot, ActivationStage } from "@/lib/types/activation";

export const ACTIVATION_MILESTONES = ["business", "receptionist", "number", "forwarding", "launch"] as const;

export type ActivationMilestoneId = (typeof ACTIVATION_MILESTONES)[number];
export type ActivationMilestoneState = "completed" | "current" | "locked";

const NUMBER_STAGES: ReadonlySet<ActivationStage> = new Set([
  "payment_required",
  "provisioning_consent_required",
  "provisioning",
  "provisioning_failed",
]);

const LAUNCH_STAGES: ReadonlySet<ActivationStage> = new Set([
  "verification_window_open",
  "ready_to_activate",
  "activating",
  "runtime_paused",
  "active",
]);

const hasText = (value: string | null) => Boolean(value?.trim());

export function isBusinessMilestoneComplete(snapshot: ActivationSnapshot): boolean {
  const { profile } = snapshot;

  return Boolean(
    hasText(profile.owner_name) &&
      hasText(profile.business_name) &&
      hasText(profile.business_type) &&
      hasText(profile.timezone) &&
      profile.business_hours &&
      hasText(profile.existing_phone_e164) &&
      profile.confirmed_carrier,
  );
}

function canonicalMilestone(snapshot: ActivationSnapshot): ActivationMilestoneId {
  if (snapshot.stage === "profile_required") {
    return isBusinessMilestoneComplete(snapshot) ? "receptionist" : "business";
  }
  if (NUMBER_STAGES.has(snapshot.stage)) {
    return "number";
  }
  if (snapshot.stage === "forwarding_required") {
    return "forwarding";
  }
  if (LAUNCH_STAGES.has(snapshot.stage)) {
    return "launch";
  }

  return "business";
}

function isMilestoneComplete(snapshot: ActivationSnapshot, milestone: ActivationMilestoneId): boolean {
  const completed = new Set(snapshot.completed_milestones);

  switch (milestone) {
    case "business":
      return completed.has("profile_confirmed") || isBusinessMilestoneComplete(snapshot);
    case "receptionist":
      return completed.has("profile_confirmed");
    case "number":
      return completed.has("number_provisioned");
    case "forwarding":
      return completed.has("forwarding_verified");
    case "launch":
      return completed.has("activated");
  }
}

export function getMilestoneState(
  snapshot: ActivationSnapshot,
  milestone: ActivationMilestoneId,
): ActivationMilestoneState {
  if (milestone === canonicalMilestone(snapshot)) {
    return "current";
  }
  return isMilestoneComplete(snapshot, milestone) ? "completed" : "locked";
}

function isMilestoneId(value: string | null): value is ActivationMilestoneId {
  return value !== null && ACTIVATION_MILESTONES.some((milestone) => milestone === value);
}

export function selectMilestone(
  snapshot: ActivationSnapshot,
  requestedMilestone: string | null,
): ActivationMilestoneId {
  const current = canonicalMilestone(snapshot);

  if (!isMilestoneId(requestedMilestone)) {
    return current;
  }

  return getMilestoneState(snapshot, requestedMilestone) === "locked" ? current : requestedMilestone;
}

export function canEnterDashboard(snapshot: ActivationSnapshot): boolean {
  return (
    snapshot.stage === "active" || (snapshot.stage === "runtime_paused" && snapshot.activation.activated_at !== null)
  );
}
