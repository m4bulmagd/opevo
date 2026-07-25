import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { BackendApiError } from "@/lib/api/backend-client";

const {
  requireServerSessionMock,
  deactivateAccountApiMock,
  activateDevelopmentStarterMock,
  createCheckoutSessionMock,
  getDevelopmentCapabilitiesMock,
  revalidatePathMock,
  redirectMock,
} = vi.hoisted(() => ({
  requireServerSessionMock: vi.fn(),
  deactivateAccountApiMock: vi.fn(),
  activateDevelopmentStarterMock: vi.fn(),
  createCheckoutSessionMock: vi.fn(),
  getDevelopmentCapabilitiesMock: vi.fn(),
  revalidatePathMock: vi.fn(),
  redirectMock: vi.fn(),
}));

vi.mock("@/lib/auth/server-session", () => ({
  requireServerSession: requireServerSessionMock,
  ServerSessionRequiredError: class ServerSessionRequiredError extends Error {},
}));
vi.mock("@/lib/api/account", () => ({
  deactivateAccount: deactivateAccountApiMock,
}));
vi.mock("@/lib/api/activation", () => ({
  activateDevelopmentStarter: activateDevelopmentStarterMock,
}));
vi.mock("@/lib/api/billing", () => ({
  createCheckoutSession: createCheckoutSessionMock,
}));
vi.mock("@/lib/development/capabilities", () => ({
  getDevelopmentCapabilities: getDevelopmentCapabilitiesMock,
}));
vi.mock("next/cache", () => ({
  revalidatePath: revalidatePathMock,
}));
vi.mock("next/navigation", () => ({
  redirect: redirectMock,
}));

import { deactivateAccount, reactivateAccount } from "@/app/(app)/dashboard/account/actions";

describe("account server actions", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    requireServerSessionMock.mockResolvedValue({ userId: "owner-1", token: "session-token" });
    getDevelopmentCapabilitiesMock.mockReturnValue({ localBilling: false, localVerification: false });
    deactivateAccountApiMock.mockResolvedValue({
      status: "deactivating",
      serving: false,
      deactivation: { state: "requested", requested_at: "2026-07-24T10:00:00Z" },
      reactivation_allowed: false,
      blocker: "account_deactivating",
    });
  });

  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("rejects anything except the exact DEACTIVATE confirmation before mutation", async () => {
    const result = await deactivateAccount("deactivate");

    expect(result).toEqual({
      status: "error",
      code: "invalid_confirmation",
      message: "Type DEACTIVATE exactly to confirm.",
    });
    expect(requireServerSessionMock).toHaveBeenCalledOnce();
    expect(deactivateAccountApiMock).not.toHaveBeenCalled();
  });

  it("authenticates, delegates deactivation to the API, revalidates lifecycle surfaces, and redirects", async () => {
    await deactivateAccount("DEACTIVATE");

    expect(requireServerSessionMock).toHaveBeenCalledOnce();
    expect(deactivateAccountApiMock).toHaveBeenCalledWith("DEACTIVATE");
    expect(revalidatePathMock.mock.calls).toEqual([
      ["/dashboard"],
      ["/dashboard/account"],
      ["/dashboard/agent"],
      ["/dashboard/billing"],
      ["/activate"],
    ]);
    expect(redirectMock).toHaveBeenCalledWith("/dashboard/account");
  });

  it("maps API failures to bounded customer-safe errors", async () => {
    deactivateAccountApiMock.mockRejectedValueOnce(
      new BackendApiError(
        {
          code: "account_inactive",
          provider_number_id: "pn_must_not_leak",
        },
        409,
      ),
    );

    const result = await deactivateAccount("DEACTIVATE");

    expect(result).toMatchObject({
      status: "error",
      code: "account_inactive",
      message: "This account is already inactive.",
    });
    expect(JSON.stringify(result)).not.toContain("pn_must_not_leak");
    expect(revalidatePathMock).not.toHaveBeenCalled();
    expect(redirectMock).not.toHaveBeenCalled();
  });

  it("reactivates through the guarded local fake billing boundary and returns to activation", async () => {
    getDevelopmentCapabilitiesMock.mockReturnValue({ localBilling: true, localVerification: false });
    activateDevelopmentStarterMock.mockResolvedValue({ stage: "payment_required" });

    const result = await reactivateAccount();

    expect(requireServerSessionMock).toHaveBeenCalledOnce();
    expect(activateDevelopmentStarterMock).toHaveBeenCalledOnce();
    expect(createCheckoutSessionMock).not.toHaveBeenCalled();
    expect(result).toEqual({
      status: "success",
      message: "Your starter plan is ready. Continue activation.",
      url: "/activate",
    });
  });

  it("reactivates through hosted checkout outside local fake billing", async () => {
    createCheckoutSessionMock.mockResolvedValue({ url: "https://checkout.stripe.test/reactivate" });

    const result = await reactivateAccount();

    expect(requireServerSessionMock).toHaveBeenCalledOnce();
    expect(createCheckoutSessionMock).toHaveBeenCalledWith("starter");
    expect(activateDevelopmentStarterMock).not.toHaveBeenCalled();
    expect(result).toEqual({
      status: "success",
      message: "Checkout is ready.",
      url: "https://checkout.stripe.test/reactivate",
    });
  });

  it("limits the Stripe test fixture to the test environment", async () => {
    vi.stubEnv("NODE_ENV", "production");
    createCheckoutSessionMock.mockResolvedValue({ url: "https://checkout.stripe.test/reactivate" });

    const result = await reactivateAccount();

    expect(result).toEqual({
      status: "error",
      code: "request_failed",
      message: "We couldn't open checkout. Refresh and try again.",
    });
  });

  it("accepts Stripe Checkout as the hosted reactivation boundary", async () => {
    createCheckoutSessionMock.mockResolvedValue({
      url: "https://checkout.stripe.com/c/pay/cs_live_123",
    });

    const result = await reactivateAccount();

    expect(result).toEqual({
      status: "success",
      message: "Checkout is ready.",
      url: "https://checkout.stripe.com/c/pay/cs_live_123",
    });
  });

  it.each([
    "http://checkout.stripe.com/c/pay/cs_secret",
    "https://provider.internal/session-secret",
    "https://checkout.stripe.com.evil.example/session-secret",
    "https://checkout.stripe.com:444/session-secret",
    "https://user:password@checkout.stripe.com/session-secret",
  ])("does not expose an untrusted hosted redirect: %s", async (unsafeUrl) => {
    createCheckoutSessionMock.mockResolvedValueOnce({ url: unsafeUrl });
    const result = await reactivateAccount();

    expect(result).toEqual({
      status: "error",
      code: "request_failed",
      message: "We couldn't open checkout. Refresh and try again.",
    });
    expect(JSON.stringify(result)).not.toContain("session-secret");
  });

  it("does not expose backend provider details", async () => {
    createCheckoutSessionMock.mockRejectedValueOnce(
      new BackendApiError({ code: "reactivation_not_ready", subscription_id: "sub_secret" }, 409),
    );
    const conflictResult = await reactivateAccount();

    expect(conflictResult).toMatchObject({
      status: "error",
      code: "reactivation_not_ready",
      message: "Reactivation is not ready yet. Refresh and try again.",
    });
    expect(JSON.stringify(conflictResult)).not.toContain("sub_secret");
  });
});
