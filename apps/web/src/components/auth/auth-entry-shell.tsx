import type { ReactNode } from "react";

import Link from "next/link";

import { PhoneCall } from "lucide-react";

export const OPEVO_CLERK_APPEARANCE = {
  variables: {
    colorPrimary: "var(--primary)",
    colorBackground: "var(--card)",
    colorText: "var(--foreground)",
    colorTextSecondary: "var(--muted-foreground)",
    colorInputBackground: "var(--background)",
    colorInputText: "var(--foreground)",
    borderRadius: "0.875rem",
    fontFamily: 'ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
  },
  elements: {
    rootBox: "w-full",
    cardBox: "w-full shadow-none",
    card: "w-full bg-transparent shadow-none p-0",
    header: "hidden",
    footer: "bg-transparent",
  },
} as const;

type AuthEntryShellProps = {
  children: ReactNode;
  description: string;
  title: string;
};

export function AuthEntryShell({ children, description, title }: AuthEntryShellProps) {
  return (
    <main className="relative grid min-h-screen place-items-center overflow-hidden bg-background px-4 py-10 sm:px-6">
      <div
        aria-hidden="true"
        className="absolute inset-x-0 top-0 h-72 bg-[radial-gradient(circle_at_top,var(--color-primary-soft),transparent_68%)] opacity-70"
      />
      <div className="relative flex w-full max-w-md flex-col gap-6">
        <Link
          aria-label="Opevo home"
          className="mx-auto inline-flex min-h-11 items-center gap-3 rounded-lg font-semibold tracking-tight outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
          href="/"
        >
          <span className="grid size-9 place-items-center rounded-xl bg-primary text-primary-foreground">
            <PhoneCall aria-hidden="true" className="size-4" />
          </span>
          Opevo
        </Link>
        <section
          aria-labelledby="auth-entry-title"
          className="rounded-2xl border border-border bg-card p-5 shadow-raised sm:p-7"
        >
          <div className="mb-5 text-center">
            <h1 className="font-semibold text-2xl tracking-tight" id="auth-entry-title">
              {title}
            </h1>
            <p className="mt-2 text-muted-foreground text-sm leading-6">{description}</p>
          </div>
          {children}
        </section>
        <p className="text-center text-muted-foreground text-xs">
          France-first call handling · Your activation progress resumes automatically.
        </p>
      </div>
    </main>
  );
}
