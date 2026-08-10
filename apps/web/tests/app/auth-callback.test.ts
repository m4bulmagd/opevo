import { NextRequest } from "next/server";

import { beforeEach, describe, expect, it, vi } from "vitest";

import { GET } from "@/app/auth/callback/route";

const { authState, exchangeCodeMock } = vi.hoisted(() => ({
  authState: { provider: "supabase" },
  exchangeCodeMock: vi.fn(),
}));

vi.mock("@/lib/auth/auth-config", () => ({
  get authProvider() {
    return authState.provider;
  },
}));

vi.mock("@/lib/auth/providers/supabase/callback", () => ({
  exchangeSupabaseAuthCode: exchangeCodeMock,
}));

describe("auth callback", () => {
  beforeEach(() => {
    authState.provider = "supabase";
    exchangeCodeMock.mockReset().mockResolvedValue(true);
  });

  it("exchanges a valid code and preserves a safe local destination", async () => {
    const response = await GET(
      new NextRequest("https://app.example/auth/callback?code=valid&next=%2Fdashboard%2Fcalls%3Frange%3D7d"),
    );

    expect(exchangeCodeMock).toHaveBeenCalledWith("valid");
    expect(response.headers.get("location")).toBe("https://app.example/dashboard/calls?range=7d");
  });

  it("falls back to the dashboard for a backslash-based external destination", async () => {
    const response = await GET(new NextRequest("https://app.example/auth/callback?code=valid&next=%2F%5Cevil.example"));

    expect(response.headers.get("location")).toBe("https://app.example/dashboard");
  });

  it("returns a bounded sign-in error when code exchange fails", async () => {
    exchangeCodeMock.mockResolvedValue(false);

    const response = await GET(new NextRequest("https://app.example/auth/callback?code=invalid"));

    expect(response.headers.get("location")).toBe("https://app.example/sign-in?error=confirmation_failed");
    expect(response.headers.get("location")).not.toContain("PRIVATE_PROVIDER_ERROR");
  });

  it("contains an unexpected provider callback failure", async () => {
    exchangeCodeMock.mockRejectedValue(new Error("PRIVATE_PROVIDER_ERROR"));

    const response = await GET(new NextRequest("https://app.example/auth/callback?code=invalid"));

    expect(response.headers.get("location")).toBe("https://app.example/sign-in?error=confirmation_failed");
  });

  it("does not exchange provider codes when the selected provider has no callback", async () => {
    authState.provider = "clerk";

    const response = await GET(new NextRequest("https://app.example/auth/callback?code=ignored&next=%2Fdashboard"));

    expect(exchangeCodeMock).not.toHaveBeenCalled();
    expect(response.headers.get("location")).toBe("https://app.example/dashboard");
  });
});
