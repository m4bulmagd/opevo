import { Activity, Bot, Clock3 } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { formatMinutes, toTitleCase } from "@/lib/formatters";
import type { AgentConfig } from "@/lib/types/agent";
import type { UsageSnapshot } from "@/lib/types/billing";
import type { CallHistoryListItem } from "@/lib/types/calls";

export function StatusSummaryCards({
  agentConfig,
  calls,
  usageSnapshot,
}: {
  agentConfig: AgentConfig | null;
  calls: CallHistoryListItem[];
  usageSnapshot: UsageSnapshot;
}) {
  const latestCall = calls[0] ?? null;

  return (
    <div className="grid gap-4 md:grid-cols-3">
      <Card size="sm">
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Bot className="size-4 text-muted-foreground" />
            Agent state
          </CardTitle>
        </CardHeader>
        <CardContent className="flex items-center justify-between gap-3">
          <div className="flex flex-col gap-1">
            <span className="font-medium">{agentConfig?.agent_name ?? "Setup required"}</span>
            <span className="text-muted-foreground text-xs">
              {agentConfig ? toTitleCase(agentConfig.pipeline_mode) : "No configuration yet"}
            </span>
          </div>
          <Badge variant={agentConfig?.is_enabled ? "default" : "secondary"}>
            {agentConfig?.is_enabled ? "Live" : "Draft"}
          </Badge>
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
