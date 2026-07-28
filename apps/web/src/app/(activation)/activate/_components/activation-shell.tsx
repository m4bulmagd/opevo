import type { ReactNode } from "react";

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
    <main className="mx-auto flex w-full max-w-3xl flex-1 flex-col px-4 py-8 sm:px-6 sm:py-12" id="activation-content">
      <StageRefresh stage={snapshot.stage} />
      <div className="flex flex-col gap-6">
        <div className="flex flex-col gap-3">
          <p className="text-label">Step {stepNumber} of 5</p>
          <MilestoneNav snapshot={snapshot} selectedMilestone={selectedMilestone} />
        </div>

        <section aria-labelledby={`${selectedMilestone}-title`}>{children}</section>
      </div>
    </main>
  );
}
