import { beforeEach, describe, expect, it, vi } from "vitest";

const {
  archiveCallMock,
  createCheckoutSessionMock,
  createPortalSessionMock,
  patchAgentConfigMock,
  retryProvisioningMock,
  revalidatePathMock,
} = vi.hoisted(() => ({
  archiveCallMock: vi.fn(),
  createCheckoutSessionMock: vi.fn(),
  createPortalSessionMock: vi.fn(),
  patchAgentConfigMock: vi.fn(),
  retryProvisioningMock: vi.fn(),
  revalidatePathMock: vi.fn(),
}));

vi.mock("next/cache", () => ({
  revalidatePath: revalidatePathMock,
}));

vi.mock("@/lib/api/agent", () => ({
  patchAgentConfig: patchAgentConfigMock,
}));

vi.mock("@/lib/api/calls", () => ({
  archiveCall: archiveCallMock,
}));

vi.mock("@/lib/api/billing", () => ({
  createCheckoutSession: createCheckoutSessionMock,
  createPortalSession: createPortalSessionMock,
}));

vi.mock("@/lib/api/onboarding", () => ({
  retryProvisioning: retryProvisioningMock,
}));

describe("dashboard action revalidation", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("revalidates agent and dashboard pages only after a successful save", async () => {
    patchAgentConfigMock.mockResolvedValueOnce({ is_enabled: true });
    const { saveAgentSettingsAction } = await import("@/app/(app)/dashboard/agent/actions");

    await saveAgentSettingsAction({ is_enabled: true });

    expect(revalidatePathMock.mock.calls).toEqual([["/dashboard/agent"], ["/dashboard"]]);

    revalidatePathMock.mockClear();
    patchAgentConfigMock.mockRejectedValueOnce(new Error("save failed"));
    await saveAgentSettingsAction({ is_enabled: false });
    expect(revalidatePathMock).not.toHaveBeenCalled();
  });

  it("retains exact successful archive invalidation and skips failed archives", async () => {
    archiveCallMock.mockResolvedValueOnce(undefined);
    const { archiveCallAction } = await import("@/app/(app)/dashboard/calls/actions");

    await archiveCallAction("call-1");

    expect(revalidatePathMock.mock.calls).toEqual([["/dashboard/calls"], ["/dashboard"]]);

    revalidatePathMock.mockClear();
    archiveCallMock.mockRejectedValueOnce(new Error("archive failed"));
    await expect(archiveCallAction("call-2")).rejects.toThrow("archive failed");
    expect(revalidatePathMock).not.toHaveBeenCalled();
  });

  it("revalidates the dashboard only after provisioning retry succeeds", async () => {
    retryProvisioningMock.mockResolvedValueOnce(undefined);
    const { retryProvisioningAction } = await import("@/app/(app)/dashboard/onboarding-actions");

    await retryProvisioningAction();

    expect(revalidatePathMock.mock.calls).toEqual([["/dashboard"]]);

    revalidatePathMock.mockClear();
    retryProvisioningMock.mockRejectedValueOnce(new Error("retry failed"));
    await retryProvisioningAction();
    expect(revalidatePathMock).not.toHaveBeenCalled();
  });

  it.each([
    "checkout",
    "portal",
  ] as const)("revalidates billing and dashboard pages only after successful %s creation", async (sessionType) => {
    const { createCheckoutSessionAction, createPortalSessionAction } = await import(
      "@/app/(app)/dashboard/billing/actions"
    );

    if (sessionType === "checkout") {
      createCheckoutSessionMock.mockResolvedValueOnce({ url: "https://checkout.test" });
      await createCheckoutSessionAction("starter");
    } else {
      createPortalSessionMock.mockResolvedValueOnce({ url: "https://portal.test" });
      await createPortalSessionAction();
    }

    expect(revalidatePathMock.mock.calls).toEqual([["/dashboard/billing"], ["/dashboard"]]);

    revalidatePathMock.mockClear();
    if (sessionType === "checkout") {
      createCheckoutSessionMock.mockRejectedValueOnce(new Error("checkout failed"));
      await createCheckoutSessionAction("starter");
    } else {
      createPortalSessionMock.mockRejectedValueOnce(new Error("portal failed"));
      await createPortalSessionAction();
    }
    expect(revalidatePathMock).not.toHaveBeenCalled();
  });
});
