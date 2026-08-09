import type { ReactElement, ReactNode } from "react";

import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ClerkSignOut } from "@/lib/auth/providers/clerk/sign-out";

vi.mock("@clerk/nextjs", async () => {
  const { Children, cloneElement } = await import("react");

  return {
    SignOutButton: ({ children }: { children: ReactNode }) => cloneElement(Children.only(children) as ReactElement),
  };
});

describe("ClerkSignOut", () => {
  it("renders one labelled activation button with a responsive text label", () => {
    render(<ClerkSignOut variant="activation" />);

    const button = screen.getByRole("button", { name: "Sign out" });
    expect(screen.getAllByRole("button")).toEqual([button]);
    expect(within(button).getByText("Sign out")).toHaveClass("hidden", "sm:inline");
  });

  it("renders one labelled workspace icon button at the existing touch size", () => {
    render(<ClerkSignOut variant="workspace" />);

    const button = screen.getByRole("button", { name: "Sign out" });
    expect(screen.getAllByRole("button")).toEqual([button]);
    expect(button).toHaveClass("size-11");
    expect(button).not.toHaveTextContent("Sign out");
  });

  it("renders one labelled full-width mobile button with visible text", () => {
    render(<ClerkSignOut variant="mobile" />);

    const button = screen.getByRole("button", { name: "Sign out" });
    expect(screen.getAllByRole("button")).toEqual([button]);
    expect(button).toHaveClass("min-h-11", "w-full", "justify-start", "px-3");
    expect(within(button).getByText("Sign out")).toBeVisible();
  });
});
