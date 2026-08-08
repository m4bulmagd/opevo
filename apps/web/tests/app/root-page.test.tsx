import type { AnchorHTMLAttributes } from "react";

import type { LinkProps } from "next/link";

import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const getServerSessionStateMock = vi.fn();

type MockLinkProps = AnchorHTMLAttributes<HTMLAnchorElement> & {
  href: string;
  prefetch?: LinkProps["prefetch"];
};

vi.mock("next/link", () => ({
  default: ({ children, href, prefetch: _prefetch, ...props }: MockLinkProps) => (
    <a href={href} {...props}>
      {children}
    </a>
  ),
}));

vi.mock("@/lib/auth/server-session", () => ({
  getServerSessionState: getServerSessionStateMock,
}));

vi.mock("motion/react", async (importOriginal) => {
  const actual = await importOriginal<typeof import("motion/react")>();

  return {
    ...actual,
    useReducedMotion: () => true,
  };
});

describe("root page", () => {
  beforeEach(() => {
    getServerSessionStateMock.mockReset();
  });

  it("shows landing-page auth actions to signed-out users", async () => {
    getServerSessionStateMock.mockResolvedValue({ isAuthenticated: false });

    const { default: Page } = await import("@/app/page");
    const { container } = render(await Page());

    expect(screen.getByRole("main")).toHaveClass("bg-background", "text-foreground");
    expect(screen.getByRole("link", { name: "Skip to main content" })).toHaveAttribute("href", "#landing-content");
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(/missed call/i);
    expect(screen.getByText(/Built for French businesses/i)).toBeVisible();
    expect(screen.getByRole("region", { name: "Product overview" })).toHaveClass(
      "rounded-2xl",
      "border",
      "bg-card",
      "shadow-raised",
    );
    expect(screen.getByRole("link", { name: /^Opevo$/i })).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: /Log in/i }).length).toBeGreaterThan(0);
    expect(screen.getAllByRole("link", { name: /Sign up/i }).length).toBeGreaterThan(0);
    expect(screen.queryByRole("link", { name: /Dashboard/i })).not.toBeInTheDocument();
    expect(container.querySelector("[data-motion='stagger']")).not.toBeNull();
    expect(container.querySelectorAll("[data-motion='fade-up']").length).toBeGreaterThan(3);
    expect(container.innerHTML).not.toContain("transition-all");
    expect(container.innerHTML).not.toMatch(/\b(?:bg|border|text)-slate-/);
    expect(container.innerHTML).not.toContain("SonicWaveformCanvas");
  });

  it("shows dashboard actions to signed-in users", async () => {
    getServerSessionStateMock.mockResolvedValue({ isAuthenticated: true });

    const { default: Page } = await import("@/app/page");
    render(await Page());

    expect(screen.getAllByRole("link", { name: /Dashboard/i }).length).toBeGreaterThan(0);
    expect(screen.queryByRole("link", { name: /Log in/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Sign up/i })).not.toBeInTheDocument();
  });
});
