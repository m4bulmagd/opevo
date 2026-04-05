import { RadioTower } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { toTitleCase } from "@/lib/formatters";
import type { AgentConfig } from "@/lib/types/agent";

export function AgentRuntimeCard({ agentConfig }: { agentConfig: AgentConfig }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Runtime state</CardTitle>
        <CardDescription>Operational status for the currently configured assistant.</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <div className="flex items-center justify-between gap-3">
          <div className="flex flex-col gap-1">
            <span className="font-medium">{agentConfig.agent_name || "Unnamed agent"}</span>
            <span className="text-muted-foreground text-xs">
              {agentConfig.owner_context || "No owner context configured yet."}
            </span>
          </div>
          <Badge variant={agentConfig.is_enabled ? "default" : "secondary"}>
            {agentConfig.is_enabled ? "Enabled" : "Disabled"}
          </Badge>
        </div>
        <div className="rounded-lg border px-3 py-3">
          <div className="flex items-center gap-2 text-muted-foreground text-xs">
            <RadioTower className="size-3.5" />
            Voice pipeline
          </div>
          <p className="mt-2 font-medium">{toTitleCase(agentConfig.pipeline_mode)}</p>
        </div>
      </CardContent>
    </Card>
  );
}
