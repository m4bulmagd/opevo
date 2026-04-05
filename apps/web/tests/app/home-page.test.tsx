import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

const getAgentConfigMock = vi.fn();
const listCallsMock = vi.fn();
const getUsageSnapshotMock = vi.fn();

vi.mock("@/lib/api/agent", () => ({
  getAgentConfig: getAgentConfigMock,
}));

vi.mock("@/lib/api/calls", () => ({
  listCalls: listCallsMock,
}));

vi.mock("@/lib/api/billing", () => ({
  getUsageSnapshot: getUsageSnapshotMock,
}));

describe("dashboard page", () => {
  it("shows setup UI for first-run users", async () => {
    getAgentConfigMock.mockResolvedValueOnce(null);
    listCallsMock.mockResolvedValueOnce([]);
    getUsageSnapshotMock.mockResolvedValueOnce({
      minutes_remaining: 0,
      allocated_minutes: 0,
      plan_tier: null,
      subscription_status: null,
      current_period_start: null,
      current_period_end: null,
    });

    const { default: Page } = await import("@/app/(app)/dashboard/page");
    render(await Page());

    expect(screen.getByText(/Setup checklist/i)).toBeInTheDocument();
    expect(screen.getByText(/Name your agent/i)).toBeInTheDocument();
    expect(screen.getByText(/No calls yet/i)).toBeInTheDocument();
    expect(screen.getByText(/No active plan/i)).toBeInTheDocument();
  });

  it("shows live activity for enabled agents", async () => {
    getAgentConfigMock.mockResolvedValueOnce({
      agent_name: "Ava",
      owner_context: "Reception for North Clinic",
      system_prompt: "Be helpful.",
      knowledge_base: "Open weekdays",
      pipeline_mode: "sts",
      is_enabled: true,
    });
    listCallsMock.mockResolvedValueOnce([
      {
        id: "call-1",
        status: "completed",
        caller_number: "+33123456789",
        started_at: "2026-03-28T10:00:00Z",
        ended_at: "2026-03-28T10:01:00Z",
        duration_seconds: 60,
        minutes_charged: 1,
        summary_text: "Caller asked about opening hours.",
        has_recording: true,
      },
    ]);
    getUsageSnapshotMock.mockResolvedValueOnce({
      minutes_remaining: 183,
      allocated_minutes: 200,
      plan_tier: "starter",
      subscription_status: "active",
      current_period_start: "2026-03-01T00:00:00Z",
      current_period_end: "2026-03-31T23:59:59Z",
    });

    const { default: Page } = await import("@/app/(app)/dashboard/page");
    render(await Page());

    expect(screen.getAllByText(/Recent call activity/i).length).toBeGreaterThan(0);
    expect(screen.getByText(/Caller asked about opening hours/i)).toBeInTheDocument();
    expect(screen.getAllByText(/Live/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/183 min/i).length).toBeGreaterThan(0);
  });
});
