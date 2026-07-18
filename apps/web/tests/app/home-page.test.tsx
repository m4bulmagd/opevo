import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const getActivationSnapshotMock = vi.fn();
const getAgentConfigMock = vi.fn();
const getOnboardingStatusMock = vi.fn();
const listCallsMock = vi.fn();
const getUsageSnapshotMock = vi.fn();

vi.mock("@/lib/api/activation", () => ({
  getActivationSnapshot: getActivationSnapshotMock,
}));

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

describe("dashboard page", () => {
  beforeEach(() => {
    getActivationSnapshotMock.mockReset().mockResolvedValue({
      stage: "active",
      activation: { activated_at: "2026-07-16T11:00:00Z" },
    });
  });

  it("shows setup UI for first-run users", async () => {
    getAgentConfigMock.mockResolvedValueOnce({
      agent_name: "Assistant",
      owner_context: null,
      system_prompt: "",
      knowledge_base: "",
      pipeline_mode: "stt_llm_tts",
      is_enabled: false,
    });
    getOnboardingStatusMock.mockResolvedValueOnce({
      subscription_status: null,
      plan_tier: null,
      minutes_remaining: 0,
      phone_number: null,
      phone_number_status: "missing",
      agent_setup_complete: false,
      can_retry_provisioning: false,
      stage: "subscription_required",
      can_activate: false,
      can_route: false,
      blockers: ["subscription_missing", "minutes_exhausted"],
      warnings: [],
      evaluated_at: "2026-07-16T12:00:00Z",
      policy_version: "runtime-v1",
    });
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
    expect(screen.getByText(/Activate billing/i)).toBeInTheDocument();
    expect(screen.getByText(/eligible for a Presvo number/i)).toBeInTheDocument();
    expect(screen.getByText(/review and confirm the provisioning details/i)).toBeInTheDocument();
    expect(screen.queryByText(/automatic\s+number\s+provisioning/i)).not.toBeInTheDocument();
    expect(screen.getByText(/Choose your plan/i)).toBeInTheDocument();
    expect(screen.getByText(/No calls yet/i)).toBeInTheDocument();
    expect(screen.getAllByText(/No active plan/i).length).toBeGreaterThan(0);
  });

  it("shows live activity for enabled agents", async () => {
    getAgentConfigMock.mockResolvedValueOnce({
      agent_name: "Ava",
      owner_context: "Reception for North Clinic",
      system_prompt: "Be helpful.",
      knowledge_base: "Open weekdays",
      pipeline_mode: "stt_llm_tts",
      is_enabled: true,
    });
    getOnboardingStatusMock.mockResolvedValueOnce({
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
