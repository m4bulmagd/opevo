import { CreditCard, ReceiptText } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { formatMinutes, toTitleCase } from "@/lib/formatters";
import type { Subscription, UsageSnapshot } from "@/lib/types/billing";

function formatCancellationDate(value: string | null): string | null {
  if (!value) {
    return null;
  }

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return null;
  }

  return new Intl.DateTimeFormat("en", {
    dateStyle: "long",
    timeZone: "UTC",
  }).format(date);
}

export function BillingSummaryCards({
  subscription,
  usageSnapshot,
}: {
  subscription: Subscription | null;
  usageSnapshot: UsageSnapshot;
}) {
  const cancellationDate = subscription?.cancel_at_period_end
    ? formatCancellationDate(subscription.cancellation_effective_at)
    : null;

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
        <CardContent className="flex items-start justify-between gap-3">
          <div className="flex flex-col gap-1.5">
            <span className="font-medium">
              {subscription?.plan_tier ? toTitleCase(subscription.plan_tier) : "No active subscription"}
            </span>
            <span className="text-muted-foreground text-xs">
              {subscription?.status ? toTitleCase(subscription.status) : "Checkout required"}
            </span>
            {subscription?.cancel_at_period_end ? (
              <div className="mt-1 flex flex-col gap-0.5">
                <span className="font-medium text-sm">Cancels at the end of your paid period</span>
                {cancellationDate ? (
                  <span className="text-muted-foreground text-xs">Effective {cancellationDate}</span>
                ) : null}
              </div>
            ) : null}
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
