import { beforeEach, describe, expect, it, vi } from "vitest";

const backendFetchMock = vi.fn();

vi.mock("@/lib/api/backend-client", () => ({
  backendFetch: backendFetchMock,
}));

describe("dashboard API client", () => {
  beforeEach(() => {
    backendFetchMock.mockReset();
  });

  it("reads metrics from the dashboard endpoint without mutation options", async () => {
    const metrics = {
      timezone: "Europe/Paris",
      calls_today: 2,
      calls_last_7_days: 8,
      calls_previous_7_days: 5,
      calls_change_from_previous_7_days: 3,
      follow_up_flagged_last_7_days: 1,
      average_duration_seconds_last_7_days: 75,
      daily_activity: [
        { date: "2026-07-20", label: "Mon", calls: 0 },
        { date: "2026-07-21", label: "Tue", calls: 1 },
        { date: "2026-07-22", label: "Wed", calls: 0 },
        { date: "2026-07-23", label: "Thu", calls: 2 },
        { date: "2026-07-24", label: "Fri", calls: 1 },
        { date: "2026-07-25", label: "Sat", calls: 1 },
        { date: "2026-07-26", label: "Sun", calls: 3 },
      ],
    };
    backendFetchMock.mockResolvedValueOnce(metrics);
    const { getDashboardMetrics } = await import("@/lib/api/dashboard");

    await expect(getDashboardMetrics()).resolves.toEqual(metrics);
    expect(backendFetchMock).toHaveBeenCalledOnce();
    expect(backendFetchMock).toHaveBeenCalledWith("/api/dashboard/metrics");
  });
});
