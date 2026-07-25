import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

export type MetricBandProps = {
  label: string;
  children: ReactNode;
};

export type MetricItemProps = {
  label: string;
  value: ReactNode;
  context?: ReactNode;
  state?: "default" | "positive" | "warning" | "negative";
};

const STATE_CLASS: Record<NonNullable<MetricItemProps["state"]>, string> = {
  default: "text-text-primary",
  positive: "text-success",
  warning: "text-warning",
  negative: "text-destructive",
};

export function MetricItem({ label, value, context, state = "default" }: MetricItemProps) {
  const isUnavailable = value === null || value === undefined;

  return (
    <div
      className="flex min-w-0 flex-col gap-2 border-border/70 border-t nth-2:border-t-0 nth-2:pt-0 pt-4 first:border-t-0 first:pt-0 md:border-t-0 md:border-l md:px-6 md:py-0 md:last:pr-0 md:first:border-l-0 md:first:pl-0"
      data-slot="metric-item"
      data-state={state}
    >
      <dt className="font-medium text-text-tertiary text-xs uppercase tracking-wide">{label}</dt>
      <dd className={cn("font-semibold text-2xl tracking-tight", STATE_CLASS[state])}>
        <span className="tabular-nums">{isUnavailable ? "Unavailable" : value}</span>
      </dd>
      {context !== undefined ? <dd className="text-text-secondary text-xs leading-relaxed">{context}</dd> : null}
    </div>
  );
}

export function MetricBand({ label, children }: MetricBandProps) {
  return (
    <section
      aria-label={label}
      className="rounded-lg border border-border/80 bg-surface px-4 py-6 shadow-raised sm:px-6"
      data-slot="metric-band"
    >
      <dl className="grid grid-cols-2 gap-6 md:auto-cols-fr md:grid-flow-col md:gap-0">{children}</dl>
    </section>
  );
}
