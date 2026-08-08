import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const getActivationSnapshotMock = vi.fn();
const getAgentConfigForRequestMock = vi.fn();
const getOnboardingStatusMock = vi.fn();
const listCallsMock = vi.fn();
const getUsageSnapshotMock = vi.fn();
const getDashboardMetricsMock = vi.fn();
const redirectMock = vi.fn(() => {
  throw new Error("NEXT_REDIRECT");
});

vi.mock("next/navigation", () => ({
  redirect: redirectMock,
}));

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

function buildAgentConfig(overrides: Record<string, unknown> = {}) {
  return {
    agent_name: "Opevo Front Desk",
    owner_context: "Reception for North Clinic",
    system_prompt: "Answer inbound calls professionally.",
    knowledge_base: "Open weekdays",
    pipeline_mode: "stt_llm_tts" as const,
    is_enabled: false,
    ...overrides,
  };
}

function buildOnboardingStatus(overrides: Record<string, unknown> = {}) {
  return {
    subscription_status: "active",
    plan_tier: "starter",
    minutes_remaining: 183,
    phone_number: null,
    phone_number_status: "provisioning",
    agent_setup_complete: false,
    can_retry_provisioning: false,
    stage: "number_provisioning",
    can_activate: false,
    can_route: false,
    blockers: ["phone_missing", "agent_setup_incomplete", "agent_disabled"],
    warnings: [],
    evaluated_at: "2026-07-16T12:00:00Z",
    policy_version: "runtime-v1",
    ...overrides,
  };
}

function buildUsageSnapshot(overrides: Record<string, unknown> = {}) {
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

function buildActivationSnapshot(overrides: Record<string, unknown> = {}) {
  return {
    stage: "active",
    activation: {
      activated_at: "2026-07-16T11:00:00Z",
    },
    ...overrides,
  };
}

describe("dashboard onboarding", () => {
  beforeEach(() => {
    getActivationSnapshotMock.mockReset().mockResolvedValue(buildActivationSnapshot());
    getAgentConfigForRequestMock.mockReset();
    getOnboardingStatusMock.mockReset();
    listCallsMock.mockReset();
    getUsageSnapshotMock.mockReset();
    getDashboardMetricsMock.mockReset().mockResolvedValue({
      timezone: "Europe/Paris",
      calls_today: 0,
      calls_last_7_days: 0,
      calls_previous_7_days: 0,
      calls_change_from_previous_7_days: 0,
      follow_up_flagged_last_7_days: 0,
      average_duration_seconds_last_7_days: null,
      daily_activity: [
        { date: "2026-07-20", label: "Mon", calls: 0 },
        { date: "2026-07-21", label: "Tue", calls: 0 },
        { date: "2026-07-22", label: "Wed", calls: 0 },
        { date: "2026-07-23", label: "Thu", calls: 0 },
        { date: "2026-07-24", label: "Fri", calls: 0 },
        { date: "2026-07-25", label: "Sat", calls: 0 },
        { date: "2026-07-26", label: "Sun", calls: 0 },
      ],
    });
    redirectMock.mockClear();
  });

  it("redirects never-activated customers before protected dashboard reads", async () => {
    getActivationSnapshotMock.mockResolvedValueOnce(buildActivationSnapshot({ stage: "profile_required" }));

    const { default: Page } = await import("@/app/(app)/dashboard/page");

    await expect(Page()).rejects.toThrow("NEXT_REDIRECT");
    expect(redirectMock).toHaveBeenCalledWith("/activate");
    expect(getAgentConfigForRequestMock).not.toHaveBeenCalled();
    expect(getOnboardingStatusMock).not.toHaveBeenCalled();
    expect(listCallsMock).not.toHaveBeenCalled();
    expect(getUsageSnapshotMock).not.toHaveBeenCalled();
    expect(getDashboardMetricsMock).not.toHaveBeenCalled();
  });

  it("allows a previously activated runtime-paused customer into the dashboard", async () => {
    getActivationSnapshotMock.mockResolvedValueOnce(
      buildActivationSnapshot({
        stage: "runtime_paused",
        activation: { activated_at: "2026-07-16T11:00:00Z" },
      }),
    );
    getAgentConfigForRequestMock.mockResolvedValueOnce(buildAgentConfig());
    getOnboardingStatusMock.mockResolvedValueOnce(buildOnboardingStatus());
    listCallsMock.mockResolvedValueOnce({
      calls: [],
      total: 0,
      limit: 5,
      offset: 0,
      has_more: false,
    });
    getUsageSnapshotMock.mockResolvedValueOnce(buildUsageSnapshot());

    const { default: Page } = await import("@/app/(app)/dashboard/page");
    render(await Page());

    expect(redirectMock).not.toHaveBeenCalled();
    expect(getAgentConfigForRequestMock).toHaveBeenCalledTimes(1);
  });

  it("renders provisioning progress clearly", async () => {
    getAgentConfigForRequestMock.mockResolvedValueOnce(buildAgentConfig());
    getOnboardingStatusMock.mockResolvedValueOnce(buildOnboardingStatus());
    listCallsMock.mockResolvedValueOnce({
      calls: [],
      total: 0,
      limit: 5,
      offset: 0,
      has_more: false,
    });
    getUsageSnapshotMock.mockResolvedValueOnce(buildUsageSnapshot());

    const { default: Page } = await import("@/app/(app)/dashboard/page");
    render(await Page());

    expect(screen.getByText(/Number provisioning in progress/i)).toBeInTheDocument();
    expect(screen.getByText(/assigning your Opevo number now/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Retry provisioning/i })).not.toBeInTheDocument();
  });

  it("shows retry and self-service guidance when provisioning fails", async () => {
    getAgentConfigForRequestMock.mockResolvedValueOnce(buildAgentConfig());
    getOnboardingStatusMock.mockResolvedValueOnce(
      buildOnboardingStatus({
        phone_number_status: "failed",
        stage: "number_provisioning_failed",
        can_retry_provisioning: true,
      }),
    );
    listCallsMock.mockResolvedValueOnce({
      calls: [],
      total: 0,
      limit: 5,
      offset: 0,
      has_more: false,
    });
    getUsageSnapshotMock.mockResolvedValueOnce(buildUsageSnapshot());

    const { default: Page } = await import("@/app/(app)/dashboard/page");
    render(await Page());

    expect(screen.getByText(/Provisioning needs attention/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Retry provisioning/i })).toBeInTheDocument();
    expect(screen.getByText(/setup will remain safely offline/i)).toBeInTheDocument();
    expect(screen.queryByText(/contact support/i)).not.toBeInTheDocument();
  });

  it("shows the assigned number when routing is ready to enable", async () => {
    getAgentConfigForRequestMock.mockResolvedValueOnce(buildAgentConfig());
    getOnboardingStatusMock.mockResolvedValueOnce(
      buildOnboardingStatus({
        phone_number: "+3315551234",
        phone_number_status: "ready",
        agent_setup_complete: true,
        stage: "ready",
        can_activate: true,
      }),
    );
    listCallsMock.mockResolvedValueOnce({
      calls: [],
      total: 0,
      limit: 5,
      offset: 0,
      has_more: false,
    });
    getUsageSnapshotMock.mockResolvedValueOnce(buildUsageSnapshot());

    const { default: Page } = await import("@/app/(app)/dashboard/page");
    render(await Page());

    expect(screen.getByText(/Ready to go live/i)).toBeInTheDocument();
    expect(screen.getAllByText(/\+3315551234/i).length).toBeGreaterThan(0);
    for (const link of screen.getAllByRole("link", { name: /Review activation/i })) {
      expect(link).toHaveAttribute("href", "/activate");
    }
  });

  it("keeps live status distinct from ready-to-enable", async () => {
    getAgentConfigForRequestMock.mockResolvedValueOnce(buildAgentConfig({ is_enabled: true }));
    getOnboardingStatusMock.mockResolvedValueOnce(
      buildOnboardingStatus({
        phone_number: "+3315551234",
        phone_number_status: "ready",
        agent_setup_complete: true,
        stage: "live",
        can_activate: true,
        can_route: true,
        blockers: [],
      }),
    );
    listCallsMock.mockResolvedValueOnce({
      calls: [],
      total: 0,
      limit: 5,
      offset: 0,
      has_more: false,
    });
    getUsageSnapshotMock.mockResolvedValueOnce(buildUsageSnapshot());

    const { default: Page } = await import("@/app/(app)/dashboard/page");
    render(await Page());

    expect(screen.getByText(/Opevo Front Desk is answering calls/i)).toBeInTheDocument();
    expect(screen.queryByText(/Ready to go live/i)).not.toBeInTheDocument();
  });
});
