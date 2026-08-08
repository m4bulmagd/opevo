import { render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AgentConfig } from "@/lib/types/agent";
import type { UsageSnapshot } from "@/lib/types/billing";
import type { CallHistoryListItem, CallHistoryListResponse } from "@/lib/types/calls";
import type { DashboardMetrics } from "@/lib/types/dashboard";
import type { OnboardingStatus } from "@/lib/types/onboarding";

const getActivationSnapshotMock = vi.fn();
const getAgentConfigForRequestMock = vi.fn();
const getOnboardingStatusMock = vi.fn();
const listCallsMock = vi.fn();
const getUsageSnapshotMock = vi.fn();
const getDashboardMetricsMock = vi.fn();

vi.mock("@/lib/api/activation", () => ({
  getActivationSnapshot: getActivationSnapshotMock,
}));

vi.mock("@/lib/api/request-data", () => ({
  getAgentConfigForRequest: getAgentConfigForRequestMock,
}));

vi.mock("@/lib/api/onboarding", () => ({
  getOnboardingStatus: getOnboardingStatusMock,
  retryProvisioning: vi.fn(),
}));

vi.mock("@/lib/api/calls", () => ({
  listCalls: listCallsMock,
}));

vi.mock("@/lib/api/billing", () => ({
  getUsageSnapshot: getUsageSnapshotMock,
}));

vi.mock("@/lib/api/dashboard", () => ({
  getDashboardMetrics: getDashboardMetricsMock,
}));

function buildAgentConfig(overrides: Partial<AgentConfig> = {}): AgentConfig {
  return {
    agent_name: "  Ava  ",
    owner_context: "Reception for North Clinic",
    system_prompt: "Answer inbound calls professionally.",
    knowledge_base: "Open weekdays",
    pipeline_mode: "stt_llm_tts",
    is_enabled: true,
    ...overrides,
  };
}

function buildOnboardingStatus(overrides: Partial<OnboardingStatus> = {}): OnboardingStatus {
  return {
    subscription_status: "active",
    plan_tier: "starter",
    minutes_remaining: 183,
    phone_number: "+3315551234",
    phone_number_status: "ready",
    agent_setup_complete: true,
    can_retry_provisioning: false,
    stage: "live",
    can_activate: true,
    can_route: true,
    blockers: [],
    warnings: [],
    evaluated_at: "2026-07-16T12:00:00Z",
    policy_version: "runtime-v1",
    ...overrides,
  };
}

function buildCall(overrides: Partial<CallHistoryListItem> = {}): CallHistoryListItem {
  return {
    id: "call-1",
    status: "completed",
    caller_number: "+33123456789",
    started_at: "2026-03-28T10:00:00Z",
    ended_at: "2026-03-28T10:02:42Z",
    duration_seconds: 162,
    minutes_charged: 3,
    summary_text: "Caller asked to book an appointment.",
    summary_status: "ready",
    caller_intent: "Book an appointment",
    action_items: ["Return the caller's message"],
    sentiment: "neutral",
    follow_up_required: true,
    has_recording: true,
    ...overrides,
  };
}

function buildCallsPage(calls: CallHistoryListItem[] = []): CallHistoryListResponse {
  return {
    calls,
    total: calls.length,
    limit: 5,
    offset: 0,
    has_more: false,
  };
}

function buildUsageSnapshot(overrides: Partial<UsageSnapshot> = {}): UsageSnapshot {
  return {
    minutes_remaining: 183,
    allocated_minutes: 200,
    plan_tier: "starter",
    subscription_status: "active",
    current_period_start: "2026-03-01T00:00:00Z",
    current_period_end: "2026-03-31T23:59:59Z",
    ...overrides,
  };
}

function buildMetrics(overrides: Partial<DashboardMetrics> = {}): DashboardMetrics {
  return {
    timezone: "Europe/Paris",
    calls_today: 8,
    calls_last_7_days: 34,
    calls_previous_7_days: 28,
    calls_change_from_previous_7_days: 6,
    follow_up_flagged_last_7_days: 3,
    average_duration_seconds_last_7_days: 162,
    daily_activity: [
      { date: "2026-07-20", label: "Mon", calls: 3 },
      { date: "2026-07-21", label: "Tue", calls: 5 },
      { date: "2026-07-22", label: "Wed", calls: 4 },
      { date: "2026-07-23", label: "Thu", calls: 6 },
      { date: "2026-07-24", label: "Fri", calls: 3 },
      { date: "2026-07-25", label: "Sat", calls: 5 },
      { date: "2026-07-26", label: "Sun", calls: 8 },
    ],
    ...overrides,
  };
}

describe("dashboard page", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-26T10:00:00Z"));

    getActivationSnapshotMock.mockReset().mockResolvedValue({
      stage: "active",
      activation: { activated_at: "2026-07-16T11:00:00Z" },
    });
    getAgentConfigForRequestMock.mockReset().mockResolvedValue(buildAgentConfig());
    getOnboardingStatusMock.mockReset().mockResolvedValue(buildOnboardingStatus());
    listCallsMock.mockReset().mockResolvedValue(
      buildCallsPage([
        buildCall(),
        buildCall({
          id: "call-2",
          caller_number: "+33987654321",
          started_at: "2026-03-28T08:15:00Z",
          ended_at: "2026-03-28T08:15:45Z",
          duration_seconds: 45,
          summary_text: "Caller checked the opening hours.",
          caller_intent: "Check opening hours",
          action_items: [],
          follow_up_required: false,
          has_recording: false,
        }),
      ]),
    );
    getUsageSnapshotMock.mockReset().mockResolvedValue(buildUsageSnapshot());
    getDashboardMetricsMock.mockReset().mockResolvedValue(buildMetrics());
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders the live operational ledger with authoritative metrics and recent-call detail", async () => {
    const { default: Page } = await import("@/app/(app)/dashboard/page");
    const { container } = render(await Page());

    expect(screen.getByRole("heading", { level: 1, name: "Operations overview" })).toBeInTheDocument();
    expect(screen.getByText("Sunday, July 26 · Europe/Paris")).toHaveAttribute("data-visual-dynamic", "true");
    expect(container.querySelectorAll("[data-visual-dynamic]")).toHaveLength(1);
    expect(screen.getByText("Ava is answering calls")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Configure receptionist" })).toHaveAttribute("href", "/dashboard/agent");
    const pageActions = container.querySelector<HTMLElement>('[data-slot="page-intro-action"]');
    expect(pageActions).not.toBeNull();
    expect(within(pageActions as HTMLElement).getByRole("link", { name: "Review billing" })).toHaveAttribute(
      "href",
      "/dashboard/billing",
    );

    const metrics = screen.getByRole("region", { name: "Operational metrics" });
    for (const label of ["Calls today", "Last 7 days", "Follow-up flagged", "Avg duration"]) {
      expect(within(metrics).getByText(label)).toBeInTheDocument();
    }
    expect(within(metrics).queryByText("Minutes remaining")).not.toBeInTheDocument();
    expect(within(metrics).getByText("8")).toBeInTheDocument();
    expect(within(metrics).getByText("34")).toBeInTheDocument();
    expect(within(metrics).getByText("3")).toBeInTheDocument();
    expect(within(metrics).getByText("2m 42s")).toBeInTheDocument();
    expect(within(metrics).getByText("+6 vs previous 7 days")).toBeInTheDocument();
    expect(within(metrics).getByText("Previous 7 days: 28 calls")).toHaveClass("sr-only");
    expect(within(metrics).queryByText(/\+?6%/)).not.toBeInTheDocument();

    const activity = screen.getByRole("region", { name: "Call activity" });
    expect(activity).toHaveAttribute("data-slot", "activity-surface");
    expect(within(activity).getByRole("region", { name: "Call activity chart" })).toBeInTheDocument();
    expect(within(activity).getByText("July 26, 2026: 8 calls")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Assigned number" })).toHaveTextContent("+3315551234");

    const recentCalls = screen.getByRole("list", { name: "Recent calls" });
    const flaggedLink = within(recentCalls).getByRole("link", { name: /^\+33123456789/i });
    const flaggedRow = flaggedLink.closest("[data-slot='dashboard-call-card']");
    expect(flaggedLink).toHaveAccessibleName(/^\+33123456789, Book an appointment, Follow-up needed, 2m 42s, Mar 28/i);
    expect(within(flaggedRow as HTMLElement).getAllByRole("link")).toHaveLength(1);
    expect(flaggedRow).toHaveTextContent("Book an appointment");
    expect(flaggedRow).toHaveTextContent("Follow-up needed");
    expect(flaggedRow).toHaveTextContent("2m 42s");
    expect(flaggedRow).toHaveTextContent("Mar 28");
    expect(flaggedRow?.querySelector("time")).toHaveAttribute("datetime", "2026-03-28T10:00:00Z");

    const standardLink = within(recentCalls).getByRole("link", { name: /^\+33987654321/i });
    const standardRow = standardLink.closest("[data-slot='dashboard-call-card']");
    expect(standardRow).toHaveTextContent("Check opening hours");
    expect(standardRow).toHaveTextContent("No follow-up needed");
    expect(standardRow).toHaveTextContent("45s");

    const attention = screen.getByRole("region", { name: "Needs attention" });
    expect(within(attention).getByText("+33123456789")).toBeInTheDocument();
    expect(within(attention).getByText("Book an appointment")).toBeInTheDocument();
    expect(within(attention).queryByText("+33987654321")).not.toBeInTheDocument();
    expect(attention).not.toHaveTextContent(/completed|resolved/i);
    expect(screen.getByRole("region", { name: "Plan usage" })).toBeInTheDocument();
  });

  it("renders a seven-day decrease as a signed integer rather than a percentage", async () => {
    getDashboardMetricsMock.mockResolvedValueOnce(
      buildMetrics({
        calls_last_7_days: 12,
        calls_previous_7_days: 17,
        calls_change_from_previous_7_days: -5,
      }),
    );

    const { default: Page } = await import("@/app/(app)/dashboard/page");
    render(await Page());

    const metrics = screen.getByRole("region", { name: "Operational metrics" });
    expect(within(metrics).getByText("-5 vs previous 7 days")).toBeInTheDocument();
    expect(within(metrics).getByText("Previous 7 days: 17 calls")).toHaveClass("sr-only");
    expect(within(metrics).queryByText(/%/)).not.toBeInTheDocument();
  });

  it("uses Receptionist for a blank configured name and preserves first-run activation guidance", async () => {
    getAgentConfigForRequestMock.mockResolvedValueOnce(
      buildAgentConfig({
        agent_name: "   ",
        owner_context: null,
        is_enabled: false,
      }),
    );
    getOnboardingStatusMock.mockResolvedValueOnce(
      buildOnboardingStatus({
        subscription_status: null,
        plan_tier: null,
        minutes_remaining: 0,
        phone_number: null,
        phone_number_status: "missing",
        agent_setup_complete: false,
        stage: "subscription_required",
        can_activate: false,
        can_route: false,
        blockers: ["subscription_missing", "minutes_exhausted"],
      }),
    );
    listCallsMock.mockResolvedValueOnce(buildCallsPage());
    getUsageSnapshotMock.mockResolvedValueOnce(
      buildUsageSnapshot({
        minutes_remaining: 0,
        allocated_minutes: 0,
        plan_tier: null,
        subscription_status: null,
        current_period_start: null,
        current_period_end: null,
      }),
    );
    getDashboardMetricsMock.mockResolvedValueOnce(
      buildMetrics({
        calls_today: 0,
        calls_last_7_days: 0,
        calls_previous_7_days: 0,
        calls_change_from_previous_7_days: 0,
        follow_up_flagged_last_7_days: 0,
        average_duration_seconds_last_7_days: null,
      }),
    );

    const { default: Page } = await import("@/app/(app)/dashboard/page");
    render(await Page());

    expect(listCallsMock).toHaveBeenCalledWith({ limit: 5 });
    expect(screen.getByText("Receptionist is paused")).toBeInTheDocument();
    expect(screen.getByText(/Setup checklist/i)).toBeInTheDocument();
    expect(screen.getByText(/Activate billing/i)).toBeInTheDocument();
    expect(screen.getByText(/eligible for a Opevo number/i)).toBeInTheDocument();
    expect(screen.getByText(/review and confirm the provisioning details/i)).toBeInTheDocument();
    expect(screen.queryByText(/automatic\s+number\s+provisioning/i)).not.toBeInTheDocument();
    expect(screen.getByText(/Choose your plan/i)).toBeInTheDocument();
    expect(screen.getByText(/No calls yet/i)).toBeInTheDocument();
    expect(screen.getAllByText(/No active plan/i).length).toBeGreaterThan(0);
    for (const link of screen.getAllByRole("link", { name: /Review activation/i })) {
      expect(link).toHaveAttribute("href", "/activate");
    }
  });

  it("isolates a metrics failure from status, calls, setup state, and plan usage", async () => {
    getDashboardMetricsMock.mockRejectedValueOnce(new Error("metrics unavailable"));
    getAgentConfigForRequestMock.mockResolvedValueOnce(buildAgentConfig({ agent_name: "Mina", is_enabled: false }));
    getOnboardingStatusMock.mockResolvedValueOnce(
      buildOnboardingStatus({
        stage: "ready",
        can_route: false,
        blockers: ["agent_disabled"],
      }),
    );

    const { default: Page } = await import("@/app/(app)/dashboard/page");
    render(await Page());

    expect(screen.getByText("Metrics temporarily unavailable")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Call activity" })).toHaveTextContent(
      "Activity data is temporarily unavailable.",
    );
    expect(screen.getByText("Date context unavailable")).toHaveAttribute("data-visual-dynamic", "true");
    expect(screen.queryByText("Europe/Paris")).not.toBeInTheDocument();
    expect(screen.getByText("Mina is paused")).toBeInTheDocument();
    expect(screen.getByRole("list", { name: "Recent calls" })).toBeInTheDocument();
    expect(screen.getByText("Ready to go live")).toBeInTheDocument();
    expect(screen.getByText("Setup checklist")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Plan usage" })).toBeInTheDocument();
  });

  it("renders honest zero and unavailable values for an empty metric window", async () => {
    getDashboardMetricsMock.mockResolvedValueOnce(
      buildMetrics({
        calls_today: 0,
        calls_last_7_days: 0,
        calls_previous_7_days: 0,
        calls_change_from_previous_7_days: 0,
        follow_up_flagged_last_7_days: 0,
        average_duration_seconds_last_7_days: null,
      }),
    );

    const { default: Page } = await import("@/app/(app)/dashboard/page");
    render(await Page());

    const metrics = screen.getByRole("region", { name: "Operational metrics" });
    for (const label of ["Calls today", "Last 7 days", "Follow-up flagged"]) {
      const item = within(metrics).getByText(label).closest("[data-slot='metric-item']");
      expect(item).toHaveTextContent("0");
    }
    const average = within(metrics).getByText("Avg duration").closest("[data-slot='metric-item']");
    expect(average).toHaveTextContent("Unavailable");
    expect(within(metrics).getByText("0 vs previous 7 days")).toBeInTheDocument();
    expect(within(metrics).getByText("Previous 7 days: 0 calls")).toHaveClass("sr-only");
  });
});
