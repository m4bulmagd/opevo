import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { toast } from "sonner";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SupabaseSignOut } from "@/lib/auth/providers/supabase/sign-out";

const { refreshMock, replaceMock, signOutMock } = vi.hoisted(() => ({
  refreshMock: vi.fn(),
  replaceMock: vi.fn(),
  signOutMock: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ refresh: refreshMock, replace: replaceMock }),
}));

vi.mock("sonner", () => ({
  toast: { error: vi.fn() },
}));

vi.mock("@/lib/auth/providers/supabase/browser-client", () => ({
  createSupabaseBrowserClient: () => ({ auth: { signOut: signOutMock } }),
}));

describe("SupabaseSignOut", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("redirects only after the provider clears the session", async () => {
    signOutMock.mockResolvedValue({ error: null });
    render(<SupabaseSignOut variant="workspace" />);

    fireEvent.click(screen.getByRole("button", { name: "Sign out" }));

    await waitFor(() => {
      expect(signOutMock).toHaveBeenCalledOnce();
      expect(replaceMock).toHaveBeenCalledWith("/");
      expect(refreshMock).toHaveBeenCalledOnce();
    });
    expect(toast.error).not.toHaveBeenCalled();
  });

  it.each([
    ["returned error", () => signOutMock.mockResolvedValue({ error: { message: "PRIVATE_PROVIDER_ERROR" } })],
    ["thrown error", () => signOutMock.mockRejectedValue(new Error("PRIVATE_PROVIDER_ERROR"))],
  ])("contains a provider %s and leaves retry available", async (_label, arrangeFailure) => {
    arrangeFailure();
    render(<SupabaseSignOut variant="workspace" />);

    fireEvent.click(screen.getByRole("button", { name: "Sign out" }));

    await waitFor(() => {
      expect(toast.error).toHaveBeenCalledWith("Unable to sign out right now. Please try again.");
      expect(screen.getByRole("button", { name: "Sign out" })).toBeEnabled();
    });
    expect(replaceMock).not.toHaveBeenCalled();
    expect(refreshMock).not.toHaveBeenCalled();
  });
});
