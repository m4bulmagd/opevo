import type { ReactNode } from "react";

import { AccountLifecycleBanner } from "@/components/account/account-lifecycle-banner";
import { CommandRail } from "@/components/workspace/command-rail";
import { MobileCommandBar } from "@/components/workspace/mobile-command-bar";
import { WorkspaceHeader } from "@/components/workspace/workspace-header";
import { authenticatedFontVariable } from "@/lib/fonts/registry";
import type { AccountStatus } from "@/lib/types/account";

type WorkspaceShellProps = {
  account: AccountStatus;
  accountControl: ReactNode;
  agentEnabled: boolean;
  agentName: string;
  children: ReactNode;
};

export function WorkspaceShell({ account, accountControl, agentEnabled, agentName, children }: WorkspaceShellProps) {
  return (
    <div
      className={`${authenticatedFontVariable} min-h-svh bg-background font-[family-name:var(--font-figtree)] text-foreground`}
      data-slot="workspace-shell"
    >
      <a
        className="sr-only rounded-md bg-background px-3 py-2 focus:not-sr-only focus:fixed focus:top-3 focus:left-3 focus:z-50 focus:ring-3 focus:ring-ring/50"
        href="#workspace-main"
      >
        Skip to workspace
      </a>
      <CommandRail agentEnabled={agentEnabled} agentName={agentName} />
      <div
        className="min-h-svh pb-[calc(4rem+env(safe-area-inset-bottom))] md:pb-0 md:pl-18 lg:pl-64"
        data-slot="workspace-content"
      >
        <WorkspaceHeader accountControl={accountControl} />
        <main
          className="mx-auto flex w-full max-w-7xl flex-col gap-5 px-4 py-5 sm:px-6 md:gap-7 md:px-8 md:py-8 lg:px-10"
          id="workspace-main"
        >
          {account.status === "active" ? null : <AccountLifecycleBanner account={account} />}
          {children}
        </main>
      </div>
      <MobileCommandBar agentName={agentName} />
    </div>
  );
}
