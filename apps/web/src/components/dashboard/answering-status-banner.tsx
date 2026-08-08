import Link from "next/link";

import { CirclePause, PhoneCall } from "lucide-react";

import { StatusSurface } from "@/components/product/status-surface";
import { Button } from "@/components/ui/button";
import type { OnboardingStatus, ReadinessBlocker } from "@/lib/types/onboarding";

const SUBSCRIPTION_BLOCKERS = new Set<ReadinessBlocker>([
  "subscription_missing",
  "plan_unsupported",
  "subscription_status_ineligible",
  "subscription_period_missing",
  "subscription_period_inactive",
]);

function pausedReason(onboardingStatus: OnboardingStatus) {
  if (onboardingStatus.blockers.includes("minutes_exhausted")) {
    return "No minutes remain. Add minutes before Opevo can answer another call.";
  }
  if (onboardingStatus.blockers.some((blocker) => SUBSCRIPTION_BLOCKERS.has(blocker))) {
    return "Your subscription needs attention before Opevo can answer calls.";
  }
  if (onboardingStatus.stage === "routing_pending") {
    return "Routing is still updating. Opevo will resume when the update completes.";
  }
  if (onboardingStatus.blockers.includes("phone_missing")) {
    return "Your Opevo number is not ready yet.";
  }
  if (
    onboardingStatus.blockers.includes("agent_config_missing") ||
    onboardingStatus.blockers.includes("agent_setup_incomplete") ||
    onboardingStatus.blockers.includes("agent_content_invalid")
  ) {
    return "Finish activation details before Opevo can answer calls.";
  }
  if (onboardingStatus.blockers.includes("agent_disabled")) {
    return "Call answering is turned off for this account.";
  }
  return "Account readiness needs attention before Opevo can answer calls.";
}

export function AnsweringStatusBanner({
  agentName,
  onboardingStatus,
}: {
  agentName: string;
  onboardingStatus: OnboardingStatus;
}) {
  if (onboardingStatus.can_route) {
    return (
      <StatusSurface
        description={`Forwarded calls can be answered by ${agentName}.`}
        icon={<PhoneCall />}
        label="Live"
        title={`${agentName} is answering calls`}
        tone="live"
      />
    );
  }

  return (
    <StatusSurface
      action={
        <Button asChild className="min-h-11" variant="outline">
          <Link href="/activate">Review activation</Link>
        </Button>
      }
      description={pausedReason(onboardingStatus)}
      icon={<CirclePause />}
      label="Paused"
      title={`${agentName} is paused`}
      tone="paused"
    />
  );
}
