import Link from "next/link";

import { Check, LockKeyhole } from "lucide-react";

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
    <nav aria-label="Activation progress" className="overflow-x-auto pb-1">
      <ol className="flex min-w-max items-center gap-1 sm:min-w-0">
        {ACTIVATION_MILESTONES.map((milestone, index) => {
          const state = getMilestoneState(snapshot, milestone);
          const label = MILESTONE_LABELS[milestone];
          const isSelected = selectedMilestone === milestone;
          const content = (
            <span
              className={cn(
                "inline-flex min-h-10 items-center gap-2 rounded-md px-2.5 text-sm outline-none transition-colors",
                "focus-visible:ring-3 focus-visible:ring-ring/50",
                state === "locked" && "text-muted-foreground",
                state !== "locked" && !isSelected && "text-foreground hover:bg-muted",
                isSelected && "bg-secondary font-medium text-secondary-foreground",
              )}
            >
              <span
                aria-hidden="true"
                className={cn(
                  "inline-flex size-5 items-center justify-center rounded-full border text-xs",
                  state === "completed" && "border-primary bg-primary text-primary-foreground",
                  state === "current" && "border-primary text-primary",
                  state === "locked" && "border-border",
                )}
              >
                {state === "completed" ? <Check /> : state === "locked" ? <LockKeyhole /> : index + 1}
              </span>
              <span>{label}</span>
              <span className="sr-only">
                {state === "completed" ? "Complete" : state === "locked" ? "Locked" : "Current"}
              </span>
            </span>
          );

          return (
            <li key={milestone} aria-current={isSelected ? "step" : undefined} className="flex items-center gap-1">
              {state === "locked" ? (
                content
              ) : (
                <Link href={`/activate?milestone=${milestone}`} className="rounded-md">
                  {content}
                </Link>
              )}
              {index < ACTIVATION_MILESTONES.length - 1 ? (
                <span aria-hidden="true" className="h-px w-3 bg-border sm:flex-1" />
              ) : null}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
