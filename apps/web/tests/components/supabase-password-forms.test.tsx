import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SupabasePasswordRecoveryForm, SupabaseUpdatePasswordForm } from "@/lib/auth/providers/supabase/password-forms";

const { refreshMock, replaceMock, resetPasswordMock, updateUserMock } = vi.hoisted(() => ({
  refreshMock: vi.fn(),
  replaceMock: vi.fn(),
  resetPasswordMock: vi.fn(),
  updateUserMock: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ refresh: refreshMock, replace: replaceMock }),
}));

vi.mock("@/lib/auth/providers/supabase/browser-client", () => ({
  createSupabaseBrowserClient: () => ({
    auth: {
      resetPasswordForEmail: resetPasswordMock,
      updateUser: updateUserMock,
    },
  }),
}));

describe("Supabase password forms", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("requests a recovery link with the local update-password callback", async () => {
    resetPasswordMock.mockResolvedValue({ error: null });
    render(<SupabasePasswordRecoveryForm />);

    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "member@example.com" } });
    fireEvent.click(screen.getByRole("button", { name: "Send reset link" }));

    expect(await screen.findByText(/Check your email for a secure password reset link/)).toBeVisible();
    expect(resetPasswordMock).toHaveBeenCalledWith("member@example.com", {
      redirectTo: "http://localhost:3000/auth/callback?next=/update-password",
    });
  });

  it("does not expose a recovery provider error", async () => {
    resetPasswordMock.mockResolvedValue({ error: { message: "PRIVATE_PROVIDER_ERROR" } });
    render(<SupabasePasswordRecoveryForm />);

    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "member@example.com" } });
    fireEvent.click(screen.getByRole("button", { name: "Send reset link" }));

    expect(await screen.findByText("Unable to send a reset link right now.")).toBeVisible();
    expect(screen.queryByText("PRIVATE_PROVIDER_ERROR")).not.toBeInTheDocument();
  });

  it("rejects mismatched passwords without calling the provider", async () => {
    render(<SupabaseUpdatePasswordForm />);

    fireEvent.change(screen.getByLabelText("New password"), { target: { value: "first-password" } });
    fireEvent.change(screen.getByLabelText("Confirm password"), { target: { value: "second-password" } });
    fireEvent.click(screen.getByRole("button", { name: "Save new password" }));

    expect(await screen.findByText("Passwords do not match.")).toBeVisible();
    expect(updateUserMock).not.toHaveBeenCalled();
  });

  it("updates a matching password and returns to account security", async () => {
    updateUserMock.mockResolvedValue({ error: null });
    render(<SupabaseUpdatePasswordForm />);

    fireEvent.change(screen.getByLabelText("New password"), { target: { value: "matching-password" } });
    fireEvent.change(screen.getByLabelText("Confirm password"), { target: { value: "matching-password" } });
    fireEvent.click(screen.getByRole("button", { name: "Save new password" }));

    await waitFor(() => {
      expect(updateUserMock).toHaveBeenCalledWith({ password: "matching-password" });
      expect(replaceMock).toHaveBeenCalledWith("/dashboard/account");
      expect(refreshMock).toHaveBeenCalledOnce();
    });
  });

  it("does not expose a password-update provider error", async () => {
    updateUserMock.mockResolvedValue({ error: { message: "PRIVATE_PROVIDER_ERROR" } });
    render(<SupabaseUpdatePasswordForm />);

    fireEvent.change(screen.getByLabelText("New password"), { target: { value: "matching-password" } });
    fireEvent.change(screen.getByLabelText("Confirm password"), { target: { value: "matching-password" } });
    fireEvent.click(screen.getByRole("button", { name: "Save new password" }));

    expect(await screen.findByText("Unable to update your password right now.")).toBeVisible();
    expect(screen.queryByText("PRIVATE_PROVIDER_ERROR")).not.toBeInTheDocument();
    expect(replaceMock).not.toHaveBeenCalled();
  });
});
