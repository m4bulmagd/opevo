import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ClerkSecurityButton } from "@/components/account/clerk-security-button";

const { openUserProfileMock } = vi.hoisted(() => ({ openUserProfileMock: vi.fn() }));

vi.mock("@clerk/nextjs", () => ({
  useClerk: () => ({ openUserProfile: openUserProfileMock }),
}));

describe("ClerkSecurityButton", () => {
  it("opens Clerk password and sign-in management only after an explicit click", () => {
    render(<ClerkSecurityButton />);

    expect(openUserProfileMock).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Manage password and sign-in" }));
    expect(openUserProfileMock).toHaveBeenCalledOnce();
  });
});
