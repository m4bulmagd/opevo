import { Bot, RadioTower } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { toTitleCase } from "@/lib/formatters";
import type { AgentConfig } from "@/lib/types/agent";

export function AgentSnapshotCard({ agentConfig }: { agentConfig: AgentConfig | null }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Bot className="size-4 text-muted-foreground" />
          Agent snapshot
        </CardTitle>
        <CardDescription>Current runtime identity and readiness summary.</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <div className="flex items-start justify-between gap-3">
          <div className="flex flex-col gap-1">
            <span className="font-medium">{agentConfig?.agent_name ?? "Agent not configured"}</span>
            <span className="text-muted-foreground text-xs">
              {agentConfig?.owner_context ?? "Add owner or business context in agent settings."}
            </span>
          </div>
          <Badge variant={agentConfig?.is_enabled ? "default" : "secondary"}>
            {agentConfig?.is_enabled ? "Enabled" : "Disabled"}
          </Badge>
        </div>
        <div className="rounded-lg border px-3 py-3">
          <div className="flex items-center gap-2 text-muted-foreground text-xs">
            <RadioTower className="size-3.5" />
            Runtime mode
          </div>
          <p className="mt-2 font-medium">
            {agentConfig ? toTitleCase(agentConfig.pipeline_mode) : "Choose a voice pipeline"}
          </p>
        </div>
      </CardContent>
    </Card>
  );
}
