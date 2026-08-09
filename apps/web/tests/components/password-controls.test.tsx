import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const authState = vi.hoisted(() => ({
  provider: "supabase" as "clerk" | "supabase",
  redirectMock: vi.fn(() => {
    throw new Error("NEXT_REDIRECT");
  }),
}));

vi.mock("next/navigation", () => ({
  redirect: authState.redirectMock,
}));

vi.mock("@/lib/auth/auth-config", () => ({
  get authProvider() {
    return authState.provider;
  },
}));

vi.mock("@/lib/auth/providers/supabase/password-forms", () => ({
  SupabasePasswordRecoveryForm: () => <div>Supabase recovery</div>,
  SupabaseUpdatePasswordForm: () => <div>Supabase update</div>,
}));

describe("password controls", () => {
  beforeEach(() => {
    authState.provider = "supabase";
    authState.redirectMock.mockClear();
  });

  it("renders the selected provider's recovery control", async () => {
    const { PasswordRecoveryControl } = await import("@/components/auth/password-controls");

    render(<PasswordRecoveryControl />);

    expect(screen.getByText("Supabase recovery")).toBeVisible();
  });

  it("redirects recovery when the selected provider lacks that capability", async () => {
    authState.provider = "clerk";
    const { PasswordRecoveryControl } = await import("@/components/auth/password-controls");

    expect(() => PasswordRecoveryControl()).toThrow("NEXT_REDIRECT");
    expect(authState.redirectMock).toHaveBeenCalledWith("/sign-in");
  });

  it("renders the selected provider's update control", async () => {
    const { PasswordUpdateControl } = await import("@/components/auth/password-controls");

    render(<PasswordUpdateControl />);

    expect(screen.getByText("Supabase update")).toBeVisible();
  });

  it("redirects updates when the selected provider lacks that capability", async () => {
    authState.provider = "clerk";
    const { requirePasswordUpdateProvider } = await import("@/components/auth/password-controls");

    expect(() => requirePasswordUpdateProvider()).toThrow("NEXT_REDIRECT");
    expect(authState.redirectMock).toHaveBeenCalledWith("/dashboard/account");
  });
});
