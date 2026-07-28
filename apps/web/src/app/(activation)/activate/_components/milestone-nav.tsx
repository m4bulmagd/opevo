import Link from "next/link";

import type { ActivationSnapshot } from "@/lib/types/activation";
import { cn } from "@/lib/utils";

import { ACTIVATION_MILESTONES, type ActivationMilestoneId, getMilestoneState } from "./stage-router";

const MILESTONE_LABELS: Record<ActivationMilestoneId, string> = {
  business: "Business",
  receptionist: "Receptionist",
  number: "Number",
  forwarding: "Forwarding",
  launch: "Launch",
};

type MilestoneNavProps = {
  snapshot: ActivationSnapshot;
  selectedMilestone: ActivationMilestoneId;
};

export function MilestoneNav({ snapshot, selectedMilestone }: MilestoneNavProps) {
  return (
    <nav aria-label="Activation progress">
      <ol className="grid grid-cols-5 gap-1.5 sm:gap-2">
        {ACTIVATION_MILESTONES.map((milestone) => {
          const state = getMilestoneState(snapshot, milestone);
          const label = MILESTONE_LABELS[milestone];
          const isSelected = selectedMilestone === milestone;
          const content = (
            <span
              className={cn(
                "flex min-w-0 flex-col gap-2 rounded-md outline-none focus-visible:ring-3 focus-visible:ring-ring/50",
                state === "locked" && "text-muted-foreground",
                state !== "locked" && "text-foreground",
                isSelected && "font-medium",
              )}
            >
              <span
                aria-hidden="true"
                data-slot="activation-progress-segment"
                className={cn(
                  "h-1.5 w-full rounded-full bg-muted transition-colors",
                  (state === "completed" || isSelected) && "bg-primary",
                )}
              />
              <span className="min-w-0 text-center text-[10px] leading-4 sm:text-xs">{label}</span>
              <span className="sr-only">
                {state === "completed" ? "Complete" : state === "locked" ? "Locked" : "Current"}
              </span>
            </span>
          );

          return (
            <li aria-current={isSelected ? "step" : undefined} className="min-w-0" key={milestone}>
              {state === "locked" ? (
                content
              ) : (
                <Link className="block min-h-11 rounded-md py-1" href={`/activate?milestone=${milestone}`}>
                  {content}
                </Link>
              )}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
