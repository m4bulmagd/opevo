import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

const getAgentConfigMock = vi.fn();
const getOnboardingStatusMock = vi.fn();
const listCallsMock = vi.fn();
const getUsageSnapshotMock = vi.fn();

vi.mock("@/lib/api/agent", () => ({
  getAgentConfig: getAgentConfigMock,
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

function buildAgentConfig(overrides: Record<string, unknown> = {}) {
  return {
    agent_name: "Presvo Front Desk",
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

describe("dashboard onboarding", () => {
  it("renders provisioning progress clearly", async () => {
    getAgentConfigMock.mockResolvedValueOnce(buildAgentConfig());
    getOnboardingStatusMock.mockResolvedValueOnce(buildOnboardingStatus());
    listCallsMock.mockResolvedValueOnce([]);
    getUsageSnapshotMock.mockResolvedValueOnce(buildUsageSnapshot());

    const { default: Page } = await import("@/app/(app)/dashboard/page");
    render(await Page());

    expect(screen.getByText(/Number provisioning in progress/i)).toBeInTheDocument();
    expect(screen.getByText(/assigning your Irish number now/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Retry provisioning/i })).not.toBeInTheDocument();
  });

  it("shows retry and self-service guidance when provisioning fails", async () => {
    getAgentConfigMock.mockResolvedValueOnce(buildAgentConfig());
    getOnboardingStatusMock.mockResolvedValueOnce(
      buildOnboardingStatus({
        phone_number_status: "failed",
        stage: "number_provisioning_failed",
        can_retry_provisioning: true,
      }),
    );
    listCallsMock.mockResolvedValueOnce([]);
    getUsageSnapshotMock.mockResolvedValueOnce(buildUsageSnapshot());

    const { default: Page } = await import("@/app/(app)/dashboard/page");
    render(await Page());

    expect(screen.getByText(/Provisioning needs attention/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Retry provisioning/i })).toBeInTheDocument();
    expect(screen.getByText(/setup will remain safely offline/i)).toBeInTheDocument();
    expect(screen.queryByText(/contact support/i)).not.toBeInTheDocument();
  });

  it("shows the assigned number when routing is ready to enable", async () => {
    getAgentConfigMock.mockResolvedValueOnce(buildAgentConfig());
    getOnboardingStatusMock.mockResolvedValueOnce(
      buildOnboardingStatus({
        phone_number: "+35315551234",
        phone_number_status: "ready",
        agent_setup_complete: true,
        stage: "ready",
        can_activate: true,
      }),
    );
    listCallsMock.mockResolvedValueOnce([]);
    getUsageSnapshotMock.mockResolvedValueOnce(buildUsageSnapshot());

    const { default: Page } = await import("@/app/(app)/dashboard/page");
    render(await Page());

    expect(screen.getByText(/Ready to go live/i)).toBeInTheDocument();
    expect(screen.getAllByText(/\+35315551234/i).length).toBeGreaterThan(0);
  });

  it("keeps live status distinct from ready-to-enable", async () => {
    getAgentConfigMock.mockResolvedValueOnce(buildAgentConfig({ is_enabled: true }));
    getOnboardingStatusMock.mockResolvedValueOnce(
      buildOnboardingStatus({
        phone_number: "+35315551234",
        phone_number_status: "ready",
        agent_setup_complete: true,
        stage: "live",
        can_activate: true,
        can_route: true,
        blockers: [],
      }),
    );
    listCallsMock.mockResolvedValueOnce([]);
    getUsageSnapshotMock.mockResolvedValueOnce(buildUsageSnapshot());

    const { default: Page } = await import("@/app/(app)/dashboard/page");
    render(await Page());

    expect(screen.getByText(/Your receptionist is live/i)).toBeInTheDocument();
    expect(screen.queryByText(/Ready to go live/i)).not.toBeInTheDocument();
  });
});
