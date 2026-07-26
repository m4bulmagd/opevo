import { MetricBand, MetricItem } from "@/components/product/metric-band";
import { ProductSurface } from "@/components/product/product-surface";
import { StatusSurface, type StatusSurfaceTone } from "@/components/product/status-surface";
import { formatMinutes, toTitleCase } from "@/lib/formatters";
import type { Subscription, UsageSnapshot } from "@/lib/types/billing";

function formatUtcDate(value: string | null): string | null {
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

function subscriptionTone(subscription: Subscription | null): StatusSurfaceTone {
  if (!subscription) {
    return "inactive";
  }
  if (subscription.cancel_at_period_end) {
    return "warning";
  }
  if (subscription.status === "active") {
    return "live";
  }
  if (subscription.status === "canceled" || subscription.status === "incomplete_expired") {
    return "inactive";
  }
  return "neutral";
}

function SubscriptionStatus({ subscription }: { subscription: Subscription | null }) {
  if (!subscription) {
    return (
      <StatusSurface
        description="Start the starter plan when you are ready to add a paid subscription."
        label="No subscription"
        title="No active subscription"
        tone="inactive"
      />
    );
  }

  const statusLabel = toTitleCase(subscription.status);
  const cancellationDate = subscription.cancel_at_period_end
    ? formatUtcDate(subscription.cancellation_effective_at)
    : null;

  return (
    <StatusSurface
      description={
        subscription.cancel_at_period_end && subscription.status === "active"
          ? "Your subscription remains active through the current paid period."
          : `Billing records this subscription as ${statusLabel.toLowerCase()}.`
      }
      label={statusLabel}
      title={`${toTitleCase(subscription.plan_tier)} subscription`}
      tone={subscriptionTone(subscription)}
    >
      {subscription.cancel_at_period_end ? (
        <div className="flex flex-col gap-1">
          <span className="font-medium text-text-primary">Cancels at the end of your paid period</span>
          {cancellationDate ? <span>Effective {cancellationDate} · UTC</span> : null}
        </div>
      ) : null}
    </StatusSurface>
  );
}

function PeriodValue({ value }: { value: string | null }) {
  const formatted = formatUtcDate(value);

  return formatted ? <time dateTime={value ?? undefined}>{formatted}</time> : "Not available";
}

export function BillingSummaryCards({
  subscription,
  usageSnapshot,
}: {
  subscription: Subscription | null;
  usageSnapshot: UsageSnapshot;
}) {
  const minutesUsed = Math.max(usageSnapshot.allocated_minutes - usageSnapshot.minutes_remaining, 0);
  const planLabel = usageSnapshot.plan_tier ? toTitleCase(usageSnapshot.plan_tier) : "No active plan";

  return (
    <>
      <SubscriptionStatus subscription={subscription} />

      <MetricBand label="Billing metrics">
        <MetricItem
          context="Available in the current billing period"
          label="Minutes remaining"
          value={formatMinutes(usageSnapshot.minutes_remaining)}
        />
        <MetricItem
          context="Calculated from the current allocation"
          label="Minutes used"
          value={formatMinutes(minutesUsed)}
        />
        <MetricItem label="Plan" value={planLabel} />
      </MetricBand>

      <ProductSurface
        description="Stored allowance and dates for the current billing period."
        title="Current period usage"
      >
        <dl className="grid grid-cols-2 gap-x-6 gap-y-5 lg:grid-cols-4">
          <div>
            <dt className="text-text-tertiary text-xs">Allocated</dt>
            <dd className="mt-1 font-medium text-sm text-text-primary">
              {formatMinutes(usageSnapshot.allocated_minutes)}
            </dd>
          </div>
          <div>
            <dt className="text-text-tertiary text-xs">Remaining</dt>
            <dd className="mt-1 font-medium text-sm text-text-primary">
              {formatMinutes(usageSnapshot.minutes_remaining)}
            </dd>
          </div>
          <div>
            <dt className="text-text-tertiary text-xs">Used</dt>
            <dd className="mt-1 font-medium text-sm text-text-primary">{formatMinutes(minutesUsed)}</dd>
          </div>
          <div>
            <dt className="text-text-tertiary text-xs">Plan</dt>
            <dd className="mt-1 font-medium text-sm text-text-primary">{planLabel}</dd>
          </div>
          <div>
            <dt className="text-text-tertiary text-xs">Period starts</dt>
            <dd className="mt-1 font-medium text-sm text-text-primary">
              <PeriodValue value={usageSnapshot.current_period_start} />
            </dd>
          </div>
          <div>
            <dt className="text-text-tertiary text-xs">Period ends</dt>
            <dd className="mt-1 font-medium text-sm text-text-primary">
              <PeriodValue value={usageSnapshot.current_period_end} />
            </dd>
          </div>
        </dl>
      </ProductSurface>
    </>
  );
}
