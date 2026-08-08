"use client";

import { useState } from "react";

import { Check, RotateCcw } from "lucide-react";

import { CapabilityBadge } from "@/components/product/capability-badge";
import { ProductSurface } from "@/components/product/product-surface";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

type ComparisonMode = "minutes" | "capabilities";
type PreviewPlanId = "starter" | "standard" | "custom";

type PreviewPlan = {
  description: string;
  features: {
    capabilities: string[];
    minutes: string[];
  };
  id: PreviewPlanId;
  name: string;
};

const PLANS: ReadonlyArray<PreviewPlan> = [
  {
    id: "starter",
    name: "Starter",
    description: "The only tier currently connected to Opevo billing.",
    features: {
      minutes: ["Live allowance comes from your billing record", "Live balance appears above"],
      capabilities: ["Hosted Stripe Checkout", "One France-first launch workspace", "Usage ledger"],
    },
  },
  {
    id: "standard",
    name: "Standard",
    description: "A planned option for higher-volume reception teams.",
    features: {
      minutes: ["Higher-volume allowance planned", "Expanded usage controls planned"],
      capabilities: ["Transfer workflows", "Priority routing", "Longer retention controls"],
    },
  },
  {
    id: "custom",
    name: "Custom",
    description: "A planned option for tailored, multi-number operations.",
    features: {
      minutes: ["Tailored allowance planned", "Multi-workspace reporting planned"],
      capabilities: ["Multiple numbers", "Custom retention", "Team administration"],
    },
  },
];

export function PlanComparisonPreview({ currentPlan }: { currentPlan: string | null }) {
  const [comparisonMode, setComparisonMode] = useState<ComparisonMode>("minutes");
  const [selectedPlan, setSelectedPlan] = useState<PreviewPlanId>("starter");

  const reset = () => {
    setComparisonMode("minutes");
    setSelectedPlan("starter");
  };

  return (
    <section aria-label="Plan comparison Preview">
      <ProductSurface
        action={<CapabilityBadge status="preview" />}
        description="Explore the template's future plan-comparison experience. Standard and Custom are not available for purchase, and every control below resets on reload."
        title="Compare plans"
      >
        <div className="mb-5 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <fieldset className="inline-flex w-fit rounded-lg border border-border bg-muted/40 p-1">
            <legend className="sr-only">Comparison view</legend>
            {(["minutes", "capabilities"] as const).map((mode) => (
              <Button
                aria-pressed={comparisonMode === mode}
                className="min-h-9"
                key={mode}
                onClick={() => setComparisonMode(mode)}
                size="sm"
                variant={comparisonMode === mode ? "secondary" : "ghost"}
              >
                Compare {mode}
              </Button>
            ))}
          </fieldset>
          <Button className="min-h-11" onClick={reset} variant="ghost">
            <RotateCcw aria-hidden data-icon="inline-start" />
            Reset plan Preview
          </Button>
        </div>

        <div aria-label="Preview plan focus" className="grid gap-4 md:grid-cols-3" role="radiogroup">
          {PLANS.map((plan) => {
            const selected = selectedPlan === plan.id;
            const isLiveTier = plan.id === "starter";
            const isCurrent = currentPlan === plan.id;

            return (
              <label
                className={cn(
                  "flex cursor-pointer flex-col rounded-2xl border bg-card p-5 shadow-card",
                  selected ? "border-primary ring-1 ring-primary/30" : "border-border hover:border-primary/40",
                )}
                key={plan.id}
              >
                <span className="flex items-start gap-3">
                  <input
                    checked={selected}
                    className="mt-1 size-4 accent-primary"
                    name="preview-plan"
                    onChange={() => setSelectedPlan(plan.id)}
                    type="radio"
                    value={plan.id}
                  />
                  <span className="min-w-0 flex-1">
                    <span className="flex flex-wrap items-center justify-between gap-2">
                      <span className="font-semibold text-sm">{plan.name}</span>
                      <CapabilityBadge status={isLiveTier ? "live" : "preview"} />
                    </span>
                    <span className="mt-1 block text-text-secondary text-xs leading-relaxed">{plan.description}</span>
                    {isCurrent ? (
                      <span className="mt-2 block font-medium text-primary text-xs">Your current tier</span>
                    ) : null}
                  </span>
                </span>

                <ul className="mt-5 flex-1 space-y-2">
                  {plan.features[comparisonMode].map((feature) => (
                    <li className="flex items-start gap-2 text-sm text-text-secondary" key={feature}>
                      <Check aria-hidden className="mt-0.5 size-4 shrink-0 text-primary" />
                      <span>{feature}</span>
                    </li>
                  ))}
                </ul>
              </label>
            );
          })}
        </div>

        <p aria-label="Plan comparison status" className="mt-4 min-h-5 text-text-secondary text-xs" role="status">
          {PLANS.find((plan) => plan.id === selectedPlan)?.name} selected for local comparison. This does not change
          your subscription.
        </p>
      </ProductSurface>
    </section>
  );
}
