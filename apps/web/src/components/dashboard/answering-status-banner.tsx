import Link from "next/link";

import { CirclePause, PhoneCall } from "lucide-react";

import { Alert, AlertAction, AlertDescription, AlertTitle } from "@/components/ui/alert";
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
    return "No minutes remain. Add minutes before Presvo can answer another call.";
  }
  if (onboardingStatus.blockers.some((blocker) => SUBSCRIPTION_BLOCKERS.has(blocker))) {
    return "Your subscription needs attention before Presvo can answer calls.";
  }
  if (onboardingStatus.stage === "routing_pending") {
    return "Routing is still updating. Presvo will resume when the update completes.";
  }
  if (onboardingStatus.blockers.includes("phone_missing")) {
    return "Your Presvo number is not ready yet.";
  }
  if (
    onboardingStatus.blockers.includes("agent_config_missing") ||
    onboardingStatus.blockers.includes("agent_setup_incomplete") ||
    onboardingStatus.blockers.includes("agent_content_invalid")
  ) {
    return "Finish activation details before Presvo can answer calls.";
  }
  if (onboardingStatus.blockers.includes("agent_disabled")) {
    return "Call answering is turned off for this account.";
  }
  return "Account readiness needs attention before Presvo can answer calls.";
}

export function AnsweringStatusBanner({ onboardingStatus }: { onboardingStatus: OnboardingStatus }) {
  if (onboardingStatus.can_route) {
    return (
      <Alert>
        <PhoneCall />
        <AlertTitle>Presvo is answering</AlertTitle>
        <AlertDescription>Forwarded calls can be answered by Presvo.</AlertDescription>
      </Alert>
    );
  }

  return (
    <Alert>
      <CirclePause />
      <AlertTitle>Presvo is paused</AlertTitle>
      <AlertDescription>{pausedReason(onboardingStatus)}</AlertDescription>
      <AlertAction>
        <Button asChild size="sm" variant="outline">
          <Link href="/activate">Review activation</Link>
        </Button>
      </AlertAction>
    </Alert>
  );
}
