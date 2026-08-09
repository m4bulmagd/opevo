import type { AnchorHTMLAttributes } from "react";

import type { LinkProps } from "next/link";

import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const authState = vi.hoisted(() => ({
  authProvider: "clerk" as "clerk" | "local",
  redirectMock: vi.fn(() => {
    throw new Error("NEXT_REDIRECT");
  }),
  signInMock: vi.fn((_props: Record<string, unknown>) => <div data-testid="clerk-sign-in" />),
  signUpMock: vi.fn((_props: Record<string, unknown>) => <div data-testid="clerk-sign-up" />),
}));

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

vi.mock("next/navigation", () => ({
  redirect: authState.redirectMock,
}));

vi.mock("@/lib/auth/auth-config", () => ({
  get authProvider() {
    return authState.authProvider;
  },
}));

vi.mock("@clerk/nextjs", () => ({
  SignIn: (props: Record<string, unknown>) => authState.signInMock(props),
  SignUp: (props: Record<string, unknown>) => authState.signUpMock(props),
}));

beforeEach(() => {
  authState.authProvider = "clerk";
  authState.redirectMock.mockClear();
  authState.signInMock.mockClear();
  authState.signUpMock.mockClear();
});

describe("hosted auth entry", () => {
  it("contains sign-in in the Opevo entry surface", async () => {
    const { default: SignInPage } = await import("@/app/(auth)/sign-in/[[...sign-in]]/page");

    render(await SignInPage());

    expect(screen.getByRole("main")).toHaveClass("bg-background");
    expect(screen.getByRole("link", { name: "Opevo home" })).toHaveAttribute("href", "/");
    expect(screen.getByRole("heading", { name: "Welcome back" })).toBeVisible();
    expect(screen.getByTestId("clerk-sign-in")).toBeVisible();
    expect(authState.signInMock.mock.calls.at(-1)?.[0]).toEqual(
      expect.objectContaining({
        appearance: expect.any(Object),
        forceRedirectUrl: "/dashboard",
        signUpUrl: "/sign-up",
      }),
    );
  });

  it("contains sign-up in the same Opevo entry surface", async () => {
    const { default: SignUpPage } = await import("@/app/(auth)/sign-up/[[...sign-up]]/page");

    render(await SignUpPage());

    expect(screen.getByRole("heading", { name: "Create your Opevo account" })).toBeVisible();
    expect(screen.getByTestId("clerk-sign-up")).toBeVisible();
    expect(authState.signUpMock.mock.calls.at(-1)?.[0]).toEqual(
      expect.objectContaining({
        appearance: expect.any(Object),
        forceRedirectUrl: "/dashboard",
        signInUrl: "/sign-in",
      }),
    );
  });

  it.each([
    ["sign-in", () => import("@/app/(auth)/sign-in/[[...sign-in]]/page")],
    ["sign-up", () => import("@/app/(auth)/sign-up/[[...sign-up]]/page")],
  ])("keeps local %s on the authoritative activation route", async (_label, loadPage) => {
    authState.authProvider = "local";
    const { default: AuthPage } = await loadPage();

    await expect(AuthPage()).rejects.toThrow("NEXT_REDIRECT");
    expect(authState.redirectMock).toHaveBeenCalledWith("/activate");
  });
});
