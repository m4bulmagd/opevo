import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { OnboardingStatusCard } from "@/components/dashboard/onboarding-status-card";
import type { OnboardingStatus } from "@/lib/types/onboarding";

const { retryProvisioningActionMock, toastSuccessMock, toastErrorMock } = vi.hoisted(() => ({
  retryProvisioningActionMock: vi.fn(),
  toastSuccessMock: vi.fn(),
  toastErrorMock: vi.fn(),
}));

vi.mock("@/app/(app)/dashboard/onboarding-actions", () => ({
  retryProvisioningAction: retryProvisioningActionMock,
}));

vi.mock("sonner", () => ({
  toast: {
    success: toastSuccessMock,
    error: toastErrorMock,
  },
}));

function buildOnboardingStatus(overrides: Partial<OnboardingStatus> = {}): OnboardingStatus {
  return {
    subscription_status: "active",
    plan_tier: "starter",
    minutes_remaining: 183,
    phone_number: null,
    phone_number_status: "failed",
    agent_setup_complete: false,
    can_retry_provisioning: true,
    stage: "number_provisioning_failed",
    can_activate: false,
    can_route: false,
    blockers: ["phone_missing", "agent_config_missing"],
    warnings: [],
    evaluated_at: "2026-07-16T12:00:00Z",
    policy_version: "runtime-v1",
    ...overrides,
  };
}

describe("onboarding status card", () => {
  it("shows an error notification when retry provisioning fails", async () => {
    retryProvisioningActionMock.mockResolvedValueOnce({
      status: "error",
      message: "Provisioning retry not allowed",
    });

    render(<OnboardingStatusCard onboardingStatus={buildOnboardingStatus()} />);

    fireEvent.click(screen.getByRole("button", { name: /Retry provisioning/i }));

    await waitFor(() => {
      expect(toastErrorMock).toHaveBeenCalledWith("Provisioning retry not allowed");
    });
  });

  it("sends customers with exhausted minutes to billing", () => {
    render(
      <OnboardingStatusCard
        onboardingStatus={buildOnboardingStatus({
          minutes_remaining: 0,
          phone_number_status: "ready",
          stage: "suspended",
          can_retry_provisioning: false,
          blockers: ["minutes_exhausted"],
        })}
      />,
    );

    expect(screen.getByText("No minutes remaining")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Manage billing/i })).toHaveAttribute("href", "/dashboard/billing");
  });

  it("sends customers with subscription blockers to billing", () => {
    render(
      <OnboardingStatusCard
        onboardingStatus={buildOnboardingStatus({
          subscription_status: "past_due",
          stage: "suspended",
          can_retry_provisioning: false,
          blockers: ["subscription_status_ineligible"],
        })}
      />,
    );

    expect(screen.getByText("Subscription needs attention")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Manage billing/i })).toHaveAttribute("href", "/dashboard/billing");
  });

  it("explains routing synchronization without offering another activation", () => {
    render(
      <OnboardingStatusCard
        onboardingStatus={buildOnboardingStatus({
          phone_number: "+3315551234",
          phone_number_status: "ready",
          agent_setup_complete: true,
          stage: "routing_pending",
          can_activate: true,
          can_retry_provisioning: false,
          blockers: ["phone_projection_inactive"],
        })}
      />,
    );

    expect(screen.getByText("Routing update in progress")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /enable|activate|go live/i })).not.toBeInTheDocument();
  });

  it("falls back safely for an unknown future blocker", () => {
    render(
      <OnboardingStatusCard
        onboardingStatus={buildOnboardingStatus({
          stage: "suspended",
          can_retry_provisioning: false,
          blockers: ["future_policy_blocker" as OnboardingStatus["blockers"][number]],
        })}
      />,
    );

    expect(screen.getByText("Your receptionist is safely offline")).toBeInTheDocument();
    expect(screen.queryByText(/is live/i)).not.toBeInTheDocument();
  });
});
