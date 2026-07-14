import Link from "next/link";

import { CheckCircle2, CircleDashed, Sparkles } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import type { AgentConfig } from "@/lib/types/agent";
import type { OnboardingStatus } from "@/lib/types/onboarding";

type ChecklistStep = {
  title: string;
  description: string;
  complete: boolean;
};

function getChecklistSteps(agentConfig: AgentConfig | null, onboardingStatus: OnboardingStatus): ChecklistStep[] {
  return [
    {
      title: "Activate billing",
      description: "Subscribe to the starter plan to unlock automatic number provisioning.",
      complete: onboardingStatus.subscription_status === "active",
    },
    {
      title: "Provision your French number",
      description: "Wait for the app to assign your live number before you enable routing.",
      complete: onboardingStatus.phone_number_status === "ready",
    },
    {
      title: "Finish agent setup",
      description: "Add a non-default agent name, business context, and prompt or knowledge base.",
      complete:
        onboardingStatus.agent_setup_complete &&
        Boolean(agentConfig?.agent_name?.trim() && agentConfig.owner_context?.trim()),
    },
    {
      title: "Enable your agent",
      description: "Switch routing live only after billing, number assignment, and setup are all complete.",
      complete: onboardingStatus.routing_enabled,
    },
  ];
}

export function SetupChecklist({
  agentConfig,
  onboardingStatus,
}: {
  agentConfig: AgentConfig | null;
  onboardingStatus: OnboardingStatus;
}) {
  const steps = getChecklistSteps(agentConfig, onboardingStatus);
  const completeCount = steps.filter((step) => step.complete).length;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Sparkles className="size-4 text-muted-foreground" />
          Setup checklist
        </CardTitle>
        <CardDescription>
          Move through the self-serve launch steps before switching your number routing live.
        </CardDescription>
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
