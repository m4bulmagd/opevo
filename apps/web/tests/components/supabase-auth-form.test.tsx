import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SupabaseAuthForm } from "@/lib/auth/providers/supabase/auth-form";

const { refreshMock, replaceMock, signInMock, signUpMock } = vi.hoisted(() => ({
  refreshMock: vi.fn(),
  replaceMock: vi.fn(),
  signInMock: vi.fn(),
  signUpMock: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ refresh: refreshMock, replace: replaceMock }),
}));

vi.mock("@/lib/auth/providers/supabase/browser-client", () => ({
  createSupabaseBrowserClient: () => ({
    auth: { signInWithPassword: signInMock, signUp: signUpMock },
  }),
}));

describe("SupabaseAuthForm", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("signs in with email and password then enters the workspace", async () => {
    signInMock.mockResolvedValue({ data: { session: { access_token: "token" } }, error: null });
    render(<SupabaseAuthForm mode="sign-in" />);

    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "member@example.com" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "strong-password" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));

    await waitFor(() => {
      expect(signInMock).toHaveBeenCalledWith({ email: "member@example.com", password: "strong-password" });
      expect(replaceMock).toHaveBeenCalledWith("/dashboard");
      expect(refreshMock).toHaveBeenCalledOnce();
    });
  });

  it("shows the email-confirmation state after signup", async () => {
    signUpMock.mockResolvedValue({ data: { session: null }, error: null });
    render(<SupabaseAuthForm mode="sign-up" />);

    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "new@example.com" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "strong-password" } });
    fireEvent.click(screen.getByRole("button", { name: "Create account" }));

    expect(await screen.findByText(/Check your email to confirm your account/)).toBeVisible();
    expect(signUpMock).toHaveBeenCalledWith(
      expect.objectContaining({ email: "new@example.com", password: "strong-password" }),
    );
    expect(replaceMock).not.toHaveBeenCalled();
  });

  it("shows a bounded provider error without navigating", async () => {
    signInMock.mockResolvedValue({ data: { session: null }, error: { message: "Invalid login credentials" } });
    render(<SupabaseAuthForm mode="sign-in" />);

    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "member@example.com" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "wrong-password" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByText("Unable to sign in with those credentials.")).toBeVisible();
    expect(screen.queryByText("Invalid login credentials")).not.toBeInTheDocument();
    expect(replaceMock).not.toHaveBeenCalled();
  });
});
