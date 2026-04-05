import { CreditCard, ReceiptText } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { formatMinutes, toTitleCase } from "@/lib/formatters";
import type { Subscription, UsageSnapshot } from "@/lib/types/billing";

export function BillingSummaryCards({
  subscription,
  usageSnapshot,
}: {
  subscription: Subscription | null;
  usageSnapshot: UsageSnapshot;
}) {
  return (
    <div className="grid gap-4 md:grid-cols-2">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <CreditCard className="size-4 text-muted-foreground" />
            Subscription
          </CardTitle>
          <CardDescription>Current local subscription state mirrored from billing.</CardDescription>
        </CardHeader>
        <CardContent className="flex items-center justify-between gap-3">
          <div className="flex flex-col gap-1">
            <span className="font-medium">
              {subscription?.plan_tier ? toTitleCase(subscription.plan_tier) : "No active subscription"}
            </span>
            <span className="text-muted-foreground text-xs">
              {subscription?.status ? toTitleCase(subscription.status) : "Checkout required"}
            </span>
          </div>
          <Badge variant={subscription?.status === "active" ? "default" : "secondary"}>
            {subscription?.status ? toTitleCase(subscription.status) : "Inactive"}
          </Badge>
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <ReceiptText className="size-4 text-muted-foreground" />
            Usage
          </CardTitle>
          <CardDescription>Minute balance and allocation for the current period.</CardDescription>
        </CardHeader>
        <CardContent className="flex items-center justify-between gap-3">
          <div className="flex flex-col gap-1">
            <span className="font-medium">{formatMinutes(usageSnapshot.minutes_remaining)}</span>
            <span className="text-muted-foreground text-xs">
              Allocated {formatMinutes(usageSnapshot.allocated_minutes)}
            </span>
          </div>
          <Badge variant="outline">
            {usageSnapshot.plan_tier ? toTitleCase(usageSnapshot.plan_tier) : "Starter state"}
          </Badge>
        </CardContent>
      </Card>
    </div>
  );
}
