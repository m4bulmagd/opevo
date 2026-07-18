import type { ReactNode } from "react";

import { Badge } from "@/components/ui/badge";
import type { ActivationSnapshot } from "@/lib/types/activation";

import { MilestoneNav } from "./milestone-nav";
import { StageRefresh } from "./stage-refresh";
import { ACTIVATION_MILESTONES, type ActivationMilestoneId } from "./stage-router";

type ActivationShellProps = {
  snapshot: ActivationSnapshot;
  selectedMilestone: ActivationMilestoneId;
  children: ReactNode;
};

export function ActivationShell({ snapshot, selectedMilestone, children }: ActivationShellProps) {
  const stepNumber = ACTIVATION_MILESTONES.indexOf(selectedMilestone) + 1;

  return (
    <main id="activation-content" className="mx-auto flex w-full max-w-5xl flex-1 flex-col px-5 py-8 sm:px-8 sm:py-12">
      <StageRefresh stage={snapshot.stage} />
      <div className="flex flex-col gap-8">
        <div className="flex flex-col gap-4">
          <div className="flex items-center justify-between gap-4">
            <p className="font-medium text-muted-foreground text-sm">Set up your missed-call receptionist</p>
            <Badge variant="outline">Step {stepNumber} of 5</Badge>
          </div>
          <MilestoneNav snapshot={snapshot} selectedMilestone={selectedMilestone} />
        </div>

        <section aria-labelledby={`${selectedMilestone}-title`} className="max-w-3xl">
          {children}
        </section>
      </div>
    </main>
  );
}
