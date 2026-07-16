import { Activity, Bot, Clock3 } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { formatMinutes, toTitleCase } from "@/lib/formatters";
import type { AgentConfig } from "@/lib/types/agent";
import type { UsageSnapshot } from "@/lib/types/billing";
import type { CallHistoryListItem } from "@/lib/types/calls";
import type { OnboardingStatus } from "@/lib/types/onboarding";

function getLaunchBadge(onboardingStatus: OnboardingStatus) {
  switch (onboardingStatus.stage) {
    case "live":
      return { label: "Live", variant: "default" as const };
    case "ready":
      return { label: "Ready", variant: "default" as const };
    case "routing_pending":
      return { label: "Updating", variant: "secondary" as const };
    case "number_provisioning_failed":
      return { label: "Action needed", variant: "secondary" as const };
    case "number_provisioning":
      return { label: "Provisioning", variant: "secondary" as const };
    case "receptionist_setup_required":
      return { label: "Setup", variant: "secondary" as const };
    case "suspended":
      return { label: "Paused", variant: "secondary" as const };
    case "subscription_required":
      return { label: "Plan needed", variant: "secondary" as const };
    default:
      return { label: "Offline", variant: "secondary" as const };
  }
}

export function StatusSummaryCards({
  agentConfig,
  onboardingStatus,
  calls,
  usageSnapshot,
}: {
  agentConfig: AgentConfig | null;
  onboardingStatus: OnboardingStatus;
  calls: CallHistoryListItem[];
  usageSnapshot: UsageSnapshot;
}) {
  const latestCall = calls[0] ?? null;
  const launchBadge = getLaunchBadge(onboardingStatus);
  const secondaryText =
    onboardingStatus.phone_number ?? (agentConfig ? toTitleCase(agentConfig.pipeline_mode) : "Starter setup required");

  return (
    <div className="grid gap-4 md:grid-cols-3">
      <Card size="sm">
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Bot className="size-4 text-muted-foreground" />
            Launch status
          </CardTitle>
        </CardHeader>
        <CardContent className="flex items-center justify-between gap-3">
          <div className="flex flex-col gap-1">
            <span className="font-medium">{agentConfig?.agent_name ?? "Setup required"}</span>
            <span className="text-muted-foreground text-xs">{secondaryText}</span>
          </div>
          <Badge variant={launchBadge.variant}>{launchBadge.label}</Badge>
        </CardContent>
      </Card>
      <Card size="sm">
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Activity className="size-4 text-muted-foreground" />
            Call activity
          </CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-1">
          <span className="font-medium">{calls.length} recent calls</span>
          <span className="text-muted-foreground text-xs">{latestCall?.status ?? "No call history yet"}</span>
        </CardContent>
      </Card>
      <Card size="sm">
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Clock3 className="size-4 text-muted-foreground" />
            Minutes remaining
          </CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-1">
          <span className="font-medium">{formatMinutes(usageSnapshot.minutes_remaining)}</span>
          <span className="text-muted-foreground text-xs">
            {usageSnapshot.plan_tier ? `${toTitleCase(usageSnapshot.plan_tier)} plan` : "No active plan"}
          </span>
        </CardContent>
      </Card>
    </div>
  );
}
