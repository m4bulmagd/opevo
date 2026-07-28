import { ChangedNumber } from "@/components/motion/changed-number";
import { MetricBand, MetricItem } from "@/components/product/metric-band";
import { formatDuration } from "@/lib/formatters";
import type { DashboardMetrics } from "@/lib/types/dashboard";

type DashboardMetricsProps = {
  metrics: DashboardMetrics | null;
};

function signedInteger(value: number) {
  return value > 0 ? `+${value}` : String(value);
}

export function DashboardMetricsBand({ metrics }: DashboardMetricsProps) {
  if (!metrics) {
    return (
      <MetricBand label="Operational metrics">
        <MetricItem
          context={<span role="status">Metrics temporarily unavailable</span>}
          label="Calls today"
          value={null}
        />
        <MetricItem label="Last 7 days" value={null} />
        <MetricItem label="Follow-up flagged" value={null} />
        <MetricItem label="Avg duration" value={null} />
      </MetricBand>
    );
  }

  return (
    <MetricBand label="Operational metrics">
      <MetricItem label="Calls today" value={<ChangedNumber value={metrics.calls_today} />} />
      <MetricItem
        context={
          <>
            <span>{signedInteger(metrics.calls_change_from_previous_7_days)} vs previous 7 days</span>
            <span className="sr-only">Previous 7 days: {metrics.calls_previous_7_days} calls</span>
          </>
        }
        label="Last 7 days"
        value={<ChangedNumber value={metrics.calls_last_7_days} />}
      />
      <MetricItem
        label="Follow-up flagged"
        state={metrics.follow_up_flagged_last_7_days > 0 ? "warning" : "default"}
        value={<ChangedNumber value={metrics.follow_up_flagged_last_7_days} />}
      />
      <MetricItem
        label="Avg duration"
        value={
          metrics.average_duration_seconds_last_7_days === null
            ? null
            : formatDuration(metrics.average_duration_seconds_last_7_days)
        }
      />
    </MetricBand>
  );
}
