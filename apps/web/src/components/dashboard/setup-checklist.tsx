import Link from "next/link";

import { CheckCircle2, CircleDashed, Sparkles } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import type { AgentConfig } from "@/lib/types/agent";

type ChecklistStep = {
  title: string;
  description: string;
  complete: boolean;
};

function getChecklistSteps(agentConfig: AgentConfig | null): ChecklistStep[] {
  return [
    {
      title: "Name your agent",
      description: "Give the assistant a clear public-facing identity.",
      complete: Boolean(agentConfig?.agent_name?.trim()),
    },
    {
      title: "Add business context",
      description: "Provide owner context or a lightweight knowledge base.",
      complete: Boolean(agentConfig?.owner_context?.trim() || agentConfig?.knowledge_base?.trim()),
    },
    {
      title: "Choose a voice pipeline",
      description: "Confirm whether this workspace should run STT/LLM/TTS or STS mode.",
      complete: Boolean(agentConfig?.pipeline_mode),
    },
    {
      title: "Enable your agent",
      description: "Switch routing live once the rest of the setup feels safe.",
      complete: Boolean(agentConfig?.is_enabled),
    },
  ];
}

export function SetupChecklist({ agentConfig }: { agentConfig: AgentConfig | null }) {
  const steps = getChecklistSteps(agentConfig);
  const completeCount = steps.filter((step) => step.complete).length;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Sparkles className="size-4 text-muted-foreground" />
          Setup checklist
        </CardTitle>
        <CardDescription>Move through the essentials before switching your number routing live.</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <div className="flex items-center justify-between rounded-lg border border-dashed px-4 py-3">
          <div className="flex flex-col gap-1">
            <span className="font-medium text-sm">Progress</span>
            <span className="text-muted-foreground text-xs">
              {completeCount} of {steps.length} tasks complete
            </span>
          </div>
          <Badge variant={completeCount === steps.length ? "default" : "secondary"}>
            {completeCount === steps.length ? "Ready" : "In progress"}
          </Badge>
        </div>
        <div className="flex flex-col gap-2">
          {steps.map((step) => (
            <div key={step.title} className="flex items-start gap-3 rounded-lg border px-4 py-3">
              {step.complete ? (
                <CheckCircle2 className="mt-0.5 size-4 text-primary" />
              ) : (
                <CircleDashed className="mt-0.5 size-4 text-muted-foreground" />
              )}
              <div className="flex flex-col gap-1">
                <span className="font-medium text-sm">{step.title}</span>
                <span className="text-muted-foreground text-xs">{step.description}</span>
              </div>
            </div>
          ))}
        </div>
      </CardContent>
      <CardFooter>
        <Button asChild>
          <Link href="/dashboard/agent">Open agent settings</Link>
        </Button>
      </CardFooter>
    </Card>
  );
}
