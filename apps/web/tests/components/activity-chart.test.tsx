import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ActivityChart } from "@/components/dashboard/activity-chart";
import type { DashboardActivityPoint } from "@/lib/types/dashboard";

const activity: DashboardActivityPoint[] = [
  { date: "2026-07-20", label: "Mon", calls: 3 },
  { date: "2026-07-21", label: "Tue", calls: 5 },
  { date: "2026-07-22", label: "Wed", calls: 4 },
  { date: "2026-07-23", label: "Thu", calls: 6 },
  { date: "2026-07-24", label: "Fri", calls: 3 },
  { date: "2026-07-25", label: "Sat", calls: 5 },
  { date: "2026-07-26", label: "Sun", calls: 8 },
];

describe("dashboard activity chart", () => {
  it("renders non-empty Recharts output and an exact text alternative", () => {
    const { container } = render(<ActivityChart data={activity} />);

    const chart = screen.getByRole("region", { name: "Call activity chart" });
    expect(chart).toHaveAttribute("data-slot", "activity-chart");
    expect(container.querySelector(".recharts-wrapper")).not.toBeNull();
    expect(container.querySelectorAll(".recharts-bar-rectangle").length).toBeGreaterThan(0);

    const summary = within(chart).getByText("Seven-day call activity");
    expect(summary).toHaveClass("sr-only");
    for (const [date, value] of [
      ["July 20, 2026", "3 calls"],
      ["July 21, 2026", "5 calls"],
      ["July 22, 2026", "4 calls"],
      ["July 23, 2026", "6 calls"],
      ["July 24, 2026", "3 calls"],
      ["July 25, 2026", "5 calls"],
      ["July 26, 2026", "8 calls"],
    ]) {
      expect(within(summary).getByText(`${date}: ${value}`)).toBeInTheDocument();
    }
  });

  it("renders honest zero values without fabricating activity", () => {
    render(<ActivityChart data={activity.map((point) => ({ ...point, calls: 0 }))} />);

    const summary = screen.getByText("Seven-day call activity");
    expect(within(summary).getAllByText(/0 calls/)).toHaveLength(7);
  });
});
