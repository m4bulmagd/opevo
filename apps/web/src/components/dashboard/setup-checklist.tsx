import Link from "next/link";

import { CheckCircle2, CircleDashed, Sparkles } from "lucide-react";

import { ProductSurface } from "@/components/product/product-surface";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
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
      description: "Activate your plan to become eligible for a Presvo number. No number is ordered yet.",
      complete: ["active", "trialing"].includes(onboardingStatus.subscription_status ?? ""),
    },
    {
      title: "Set up your Presvo number",
      description: "Review and confirm the provisioning details before Presvo assigns your number.",
      complete: onboardingStatus.phone_number_status === "ready",
    },
    {
      title: "Finish receptionist setup",
      description: "Add a receptionist name, business context, and call-handling instructions.",
      complete:
        onboardingStatus.agent_setup_complete &&
        Boolean(agentConfig?.agent_name?.trim() && agentConfig.owner_context?.trim()),
    },
    {
      title: "Enable your receptionist",
      description: "Switch routing live only after billing, number assignment, and setup are all complete.",
      complete: onboardingStatus.can_route,
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
    <ProductSurface
      description="Move through the self-serve launch steps before switching your number routing live."
      footer={
        <Button asChild className="min-h-11">
          <Link href="/activate">Review activation</Link>
        </Button>
      }
      title={
        <span className="flex items-center gap-2">
          <Sparkles className="size-4 text-muted-foreground" />
          Setup checklist
        </span>
      }
    >
      <div className="flex flex-col gap-3">
        <div className="flex items-center justify-between rounded-lg border border-dashed px-4 py-3">
          <div className="flex flex-col gap-1">
            <span className="font-medium text-sm">Progress</span>
            <span className="text-text-secondary text-xs">
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
                <span className="text-text-secondary text-xs">{step.description}</span>
              </div>
            </div>
          ))}
        </div>
      </div>
    </ProductSurface>
  );
}
