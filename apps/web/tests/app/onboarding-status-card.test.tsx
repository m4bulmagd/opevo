import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { OnboardingStatusCard } from "@/components/dashboard/onboarding-status-card";

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

function buildOnboardingStatus(overrides: Record<string, unknown> = {}) {
  return {
    subscription_status: "active",
    plan_tier: "starter",
    minutes_remaining: 183,
    phone_number: null,
    phone_number_status: "failed",
    routing_enabled: false,
    agent_setup_complete: false,
    overall_status: "provisioning_failed",
    can_retry_provisioning: true,
    ...overrides,
  } as const;
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
});
