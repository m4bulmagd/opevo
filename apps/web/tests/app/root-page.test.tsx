import type { AnchorHTMLAttributes } from "react";

import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const getServerSessionStateMock = vi.fn();

vi.mock("next/link", () => ({
  default: ({
    children,
    href,
    prefetch: _prefetch,
    ...props
  }: AnchorHTMLAttributes<HTMLAnchorElement> & { href: string }) => (
    <a href={href} {...props}>
      {children}
    </a>
  ),
}));

vi.mock("@/lib/auth/server-session", () => ({
  getServerSessionState: getServerSessionStateMock,
}));

describe("root page", () => {
  beforeEach(() => {
    getServerSessionStateMock.mockReset();
  });

  it("shows landing-page auth actions to signed-out users", async () => {
    getServerSessionStateMock.mockResolvedValue({ isAuthenticated: false });

    const { default: Page } = await import("@/app/page");
    const { container } = render(await Page());

    expect(screen.getByRole("link", { name: /^Presvo$/i })).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: /Log in/i }).length).toBeGreaterThan(0);
    expect(screen.getAllByRole("link", { name: /Sign up/i }).length).toBeGreaterThan(0);
    expect(screen.queryByRole("link", { name: /Dashboard/i })).not.toBeInTheDocument();
    expect(container.querySelector("[data-motion='stagger']")).not.toBeNull();
    expect(container.querySelectorAll("[data-motion='fade-up']").length).toBeGreaterThan(3);
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
