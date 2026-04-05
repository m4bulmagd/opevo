import { beforeEach, describe, expect, it, vi } from "vitest";

const redirectMock = vi.fn();
const getServerSessionStateMock = vi.fn();

vi.mock("next/navigation", () => ({
  redirect: redirectMock,
}));

vi.mock("@/lib/auth/server-session", () => ({
  getServerSessionState: getServerSessionStateMock,
}));

describe("root page", () => {
  beforeEach(() => {
    redirectMock.mockReset();
    getServerSessionStateMock.mockReset();
  });

  it("sends signed-out users to sign-in", async () => {
    getServerSessionStateMock.mockResolvedValue({ isAuthenticated: false });

    const { default: Page } = await import("@/app/page");
    await Page();

    expect(redirectMock).toHaveBeenCalledWith("/sign-in");
  });

  it("sends signed-in users to /dashboard", async () => {
    getServerSessionStateMock.mockResolvedValue({ isAuthenticated: true });

    const { default: Page } = await import("@/app/page");
    await Page();

    expect(redirectMock).toHaveBeenCalledWith("/dashboard");
  });
});
