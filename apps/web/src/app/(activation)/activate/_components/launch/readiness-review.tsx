import Link from "next/link";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";

const EXPECTED_GO_LIVE_PROJECTION_BLOCKERS = new Set([
  "agent_disabled",
  "phone_inactive",
  "phone_projection_inactive",
  "go_live_not_approved",
  "go_live_not_activated",
]);

const BLOCKER_DESTINATIONS: Record<string, string> = {
  subscription_missing: "/activate?milestone=number",
  plan_unsupported: "/dashboard/billing",
  subscription_status_ineligible: "/dashboard/billing",
  subscription_period_missing: "/dashboard/billing",
  subscription_period_inactive: "/dashboard/billing",
  minutes_exhausted: "/dashboard/billing",
  phone_missing: "/activate?milestone=number",
  phone_provider_id_missing: "/activate?milestone=number",
  agent_config_missing: "/activate?milestone=receptionist",
  agent_setup_incomplete: "/activate?milestone=receptionist",
  agent_content_invalid: "/activate?milestone=receptionist",
  business_profile_incomplete: "/activate?milestone=business",
  profile_projection_stale: "/activate?milestone=receptionist",
  forwarding_not_verified: "/activate?milestone=forwarding",
};

export function getActionableReadinessBlockers(blockers: string[]): string[] {
  return blockers.filter((blocker) => !EXPECTED_GO_LIVE_PROJECTION_BLOCKERS.has(blocker));
}

function humanizeBlocker(blocker: string): string {
  return blocker.replaceAll("_", " ");
}

function recoveryGuidance(blocker: string): string {
  if (blocker === "user_inactive") {
    return "Sign out, then sign back in with an active Opevo account.";
  }
  return "Refresh the snapshot before retrying. Opevo will remain offline while this check is unresolved.";
}

export function ReadinessReview({ blockers }: { blockers: string[] }) {
  const actionableBlockers = getActionableReadinessBlockers(blockers);

  if (actionableBlockers.length === 0) {
    return (
      <Alert role="region" aria-label="Readiness review">
        <AlertTitle>Ready for your approval</AlertTitle>
        <AlertDescription>Opevo has the verified forwarding and setup details needed to start.</AlertDescription>
      </Alert>
    );
  }

  return (
    <Alert role="region" aria-label="Readiness review" variant="destructive">
      <AlertTitle>Complete these checks before going live</AlertTitle>
      <AlertDescription>
        <ul className="mt-2 list-disc space-y-2 pl-5">
          {actionableBlockers.map((blocker) => {
            const destination = BLOCKER_DESTINATIONS[blocker];
            return (
              <li key={blocker}>
                {destination ? (
                  <Link href={destination}>{humanizeBlocker(blocker)}</Link>
                ) : (
                  <span>
                    {humanizeBlocker(blocker)}: {recoveryGuidance(blocker)}
                  </span>
                )}
              </li>
            );
          })}
        </ul>
      </AlertDescription>
    </Alert>
  );
}
