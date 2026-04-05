import { CreditCard } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { formatMinutes, toTitleCase } from "@/lib/formatters";
import type { UsageSnapshot } from "@/lib/types/billing";

export function UsageSummaryCard({ usageSnapshot }: { usageSnapshot: UsageSnapshot }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <CreditCard className="size-4 text-muted-foreground" />
          Usage summary
        </CardTitle>
        <CardDescription>Subscription and minute state from the billing API.</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <div className="flex items-end justify-between gap-3">
          <div className="flex flex-col gap-1">
            <span className="font-semibold text-2xl">{formatMinutes(usageSnapshot.minutes_remaining)}</span>
            <span className="text-muted-foreground text-xs">Remaining in the current period</span>
          </div>
          <Badge variant={usageSnapshot.subscription_status === "active" ? "default" : "secondary"}>
            {usageSnapshot.subscription_status ? toTitleCase(usageSnapshot.subscription_status) : "Unsubscribed"}
          </Badge>
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="rounded-lg border px-3 py-3">
            <p className="text-muted-foreground text-xs">Allocated</p>
            <p className="mt-1 font-medium">{formatMinutes(usageSnapshot.allocated_minutes)}</p>
          </div>
          <div className="rounded-lg border px-3 py-3">
            <p className="text-muted-foreground text-xs">Plan tier</p>
            <p className="mt-1 font-medium">
              {usageSnapshot.plan_tier ? toTitleCase(usageSnapshot.plan_tier) : "No plan"}
            </p>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
