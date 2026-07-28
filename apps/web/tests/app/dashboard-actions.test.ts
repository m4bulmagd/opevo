import { beforeEach, describe, expect, it, vi } from "vitest";

const {
  deleteCallMock,
  createCheckoutSessionMock,
  createPortalSessionMock,
  patchAgentConfigMock,
  retryProvisioningMock,
  revalidatePathMock,
  redirectMock,
} = vi.hoisted(() => ({
  deleteCallMock: vi.fn(),
  createCheckoutSessionMock: vi.fn(),
  createPortalSessionMock: vi.fn(),
  patchAgentConfigMock: vi.fn(),
  retryProvisioningMock: vi.fn(),
  revalidatePathMock: vi.fn(),
  redirectMock: vi.fn(() => {
    throw new Error("NEXT_REDIRECT");
  }),
}));

vi.mock("next/cache", () => ({
  revalidatePath: revalidatePathMock,
}));

vi.mock("next/navigation", () => ({
  redirect: redirectMock,
}));

vi.mock("@/lib/api/agent", () => ({
  patchAgentConfig: patchAgentConfigMock,
}));

vi.mock("@/lib/api/calls", () => ({
  deleteCall: deleteCallMock,
}));

vi.mock("@/lib/api/billing", () => ({
  createCheckoutSession: createCheckoutSessionMock,
  createPortalSession: createPortalSessionMock,
}));

vi.mock("@/lib/api/activation", () => ({
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

  it("redirects after successful deletion and returns retry guidance without invalidation on failure", async () => {
    deleteCallMock.mockResolvedValueOnce(undefined);
    const { deleteCallAction } = await import("@/app/(app)/dashboard/calls/actions");

    await expect(deleteCallAction("call-1")).rejects.toThrow("NEXT_REDIRECT");

    expect(revalidatePathMock.mock.calls).toEqual([["/dashboard/calls"], ["/dashboard"]]);
    expect(redirectMock).toHaveBeenCalledWith("/dashboard/calls");

    revalidatePathMock.mockClear();
    redirectMock.mockClear();
    deleteCallMock.mockRejectedValueOnce(new Error("delete failed"));
    await expect(deleteCallAction("call-2")).resolves.toEqual({
      status: "error",
      message: "Presvo could not remove this call right now. Try again.",
    });
    expect(revalidatePathMock).not.toHaveBeenCalled();
    expect(redirectMock).not.toHaveBeenCalled();
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
