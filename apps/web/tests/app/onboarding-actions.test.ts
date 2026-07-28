import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { requireServerSessionMock, revalidatePathMock } = vi.hoisted(() => ({
  requireServerSessionMock: vi.fn(),
  revalidatePathMock: vi.fn(),
}));

vi.mock("@/lib/auth/server-session", () => ({
  requireServerSession: requireServerSessionMock,
}));

vi.mock("next/cache", () => ({
  revalidatePath: revalidatePathMock,
}));

describe("dashboard onboarding actions", () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    requireServerSessionMock.mockReset().mockResolvedValue({
      userId: "user_123",
      token: "session-token",
    });
    revalidatePathMock.mockReset();
    fetchMock.mockReset().mockResolvedValue({
      ok: true,
      status: 202,
      json: vi.fn().mockResolvedValue({ stage: "provisioning" }),
    });
    vi.stubGlobal("fetch", fetchMock);
    vi.stubEnv("API_BASE_URL", "");
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "");
  });

  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
  });

  it("uses the canonical activation retry command", async () => {
    const { retryProvisioningAction } = await import("@/app/(app)/dashboard/onboarding-actions");

    await expect(retryProvisioningAction()).resolves.toEqual({
      status: "success",
      message: "Provisioning retry queued. Check back shortly.",
    });
    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8000/api/activation/retry-provisioning",
      expect.objectContaining({ method: "POST" }),
    );
    expect(revalidatePathMock).toHaveBeenCalledWith("/dashboard");
  });
});
