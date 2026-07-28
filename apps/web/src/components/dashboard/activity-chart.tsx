"use client";

import { useId } from "react";

import { Bar, BarChart, CartesianGrid, Cell, XAxis, YAxis } from "recharts";

import { ChartContainer, ChartTooltip, ChartTooltipContent } from "@/components/ui/chart";
import type { DashboardActivityPoint } from "@/lib/types/dashboard";

const chartConfig = {
  calls: {
    label: "Calls",
    color: "var(--color-chart-1)",
  },
} as const;

function formatActivityDate(value: string) {
  return new Intl.DateTimeFormat("en-US", {
    day: "numeric",
    month: "long",
    timeZone: "UTC",
    year: "numeric",
  }).format(new Date(`${value}T12:00:00Z`));
}

export function ActivityChart({ data }: { data: DashboardActivityPoint[] }) {
  const gradientId = `presvo-activity-${useId().replaceAll(":", "")}`;
  const maximum = Math.max(...data.map((point) => point.calls), 0);

  return (
    <section aria-label="Call activity chart" data-slot="activity-chart">
      <ChartContainer
        className="aspect-auto h-[16.25rem] w-full"
        config={chartConfig}
        initialDimension={{ width: 720, height: 260 }}
      >
        <BarChart data={data} margin={{ top: 8, right: 8, left: -18, bottom: 0 }} barCategoryGap={18}>
          <defs>
            <linearGradient id={gradientId} x1="0" x2="0" y1="0" y2="1">
              <stop offset="0%" stopColor="var(--color-chart-2)" stopOpacity={0.32} />
              <stop offset="100%" stopColor="var(--color-chart-1)" stopOpacity={0.96} />
            </linearGradient>
          </defs>
          <CartesianGrid stroke="var(--color-border)" strokeDasharray="3 3" vertical={false} />
          <XAxis
            axisLine={false}
            dataKey="label"
            tick={{ fill: "var(--color-muted-foreground)", fontSize: 12 }}
            tickLine={false}
          />
          <YAxis
            allowDecimals={false}
            axisLine={false}
            tick={{ fill: "var(--color-muted-foreground)", fontSize: 12 }}
            tickLine={false}
            width={44}
          />
          <ChartTooltip
            content={<ChartTooltipContent hideLabel />}
            cursor={{ fill: "var(--color-muted)", opacity: 0.5 }}
          />
          <Bar dataKey="calls" fill={`url(#${gradientId})`} isAnimationActive={false} radius={[8, 8, 8, 8]}>
            {data.map((point) => (
              <Cell fillOpacity={maximum > 0 && point.calls === maximum ? 1 : 0.72} key={point.date} />
            ))}
          </Bar>
        </BarChart>
      </ChartContainer>
      <div className="sr-only">
        Seven-day call activity
        <ol>
          {data.map((point) => (
            <li key={point.date}>
              {formatActivityDate(point.date)}: {point.calls} {point.calls === 1 ? "call" : "calls"}
            </li>
          ))}
        </ol>
      </div>
    </section>
  );
}
