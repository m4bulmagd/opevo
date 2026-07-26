import Link from "next/link";

import { ArrowRight } from "lucide-react";

import { ProductSurface } from "@/components/product/product-surface";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { formatMinutes, toTitleCase } from "@/lib/formatters";
import type { UsageSnapshot } from "@/lib/types/billing";

export function PlanUsageSurface({ usageSnapshot }: { usageSnapshot: UsageSnapshot }) {
  const hasPlan = usageSnapshot.plan_tier !== null;
  const subscriptionLabel = hasPlan
    ? usageSnapshot.subscription_status
      ? toTitleCase(usageSnapshot.subscription_status)
      : "Status unavailable"
    : "No active plan";

  return (
    <ProductSurface
      action={
        <Badge variant={usageSnapshot.subscription_status === "active" ? "default" : "secondary"}>
          {subscriptionLabel}
        </Badge>
      }
      description="Your current billing-period allowance."
      footer={
        <Button asChild className="min-h-11" variant="ghost">
          <Link href="/dashboard/billing">
            Review billing
            <ArrowRight data-icon="inline-end" />
          </Link>
        </Button>
      }
      title="Plan usage"
    >
      <dl className="grid grid-cols-2 gap-x-6 gap-y-5">
        <div className="col-span-2">
          <dt className="font-medium text-text-tertiary text-xs uppercase tracking-wide">Minutes remaining</dt>
          <dd className="mt-2 font-semibold text-2xl text-text-primary tracking-tight">
            {formatMinutes(usageSnapshot.minutes_remaining)}
          </dd>
        </div>
        <div>
          <dt className="text-text-tertiary text-xs">Allocated</dt>
          <dd className="mt-1 font-medium text-sm text-text-primary">
            {formatMinutes(usageSnapshot.allocated_minutes)}
          </dd>
        </div>
        <div>
          <dt className="text-text-tertiary text-xs">Plan</dt>
          <dd className="mt-1 font-medium text-sm text-text-primary">
            {hasPlan ? `${toTitleCase(usageSnapshot.plan_tier)} plan` : "No active plan"}
          </dd>
        </div>
      </dl>
    </ProductSurface>
  );
}
