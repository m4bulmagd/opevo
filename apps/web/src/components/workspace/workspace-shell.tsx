import type { ReactNode } from "react";

import { AccountLifecycleBanner } from "@/components/account/account-lifecycle-banner";
import { CommandRail } from "@/components/workspace/command-rail";
import type { WorkspaceCallerIdentity } from "@/components/workspace/workspace-caller-status";
import { type WorkspaceAccountControls, WorkspaceHeader } from "@/components/workspace/workspace-header";
import type { AccountStatus } from "@/lib/types/account";

type WorkspaceShellProps = {
  account: AccountStatus;
  accountControls: WorkspaceAccountControls;
  activeCaller: WorkspaceCallerIdentity | null;
  agentEnabled: boolean;
  agentName: string;
  children: ReactNode;
};

function commandRailRuntimeState(account: AccountStatus, agentEnabled: boolean) {
  if (account.deactivation?.state === "attention_required" || account.blocker === "deactivation_attention_required") {
    return "Attention required" as const;
  }
  if (account.status === "deactivating") return "Deactivating" as const;
  if (account.status === "inactive") return "Inactive" as const;
  if (!agentEnabled || !account.serving) return "Paused" as const;
  return "Enabled" as const;
}

export function WorkspaceShell({
  account,
  accountControls,
  activeCaller,
  agentEnabled,
  agentName,
  children,
}: WorkspaceShellProps) {
  return (
    <div className="min-h-svh bg-background font-sans text-foreground" data-slot="workspace-shell">
      <a
        className="sr-only rounded-md bg-background px-3 py-2 focus:not-sr-only focus:fixed focus:top-3 focus:left-3 focus:z-50 focus:ring-3 focus:ring-ring/50"
        href="#workspace-main"
      >
        Skip to workspace
      </a>
      <div className="min-h-svh lg:flex lg:gap-4 lg:p-4" data-slot="workspace-content">
        <CommandRail agentName={agentName} runtimeState={commandRailRuntimeState(account, agentEnabled)} />
        <div className="flex min-w-0 flex-1 flex-col">
          <WorkspaceHeader accountControls={accountControls} activeCaller={activeCaller} agentName={agentName} />
          <main
            className="flex w-full flex-col gap-5 px-4 py-5 sm:px-6 md:gap-7 md:px-8 md:py-8 lg:px-0"
            id="workspace-main"
          >
            {account.status === "active" ? null : <AccountLifecycleBanner account={account} />}
            {children}
          </main>
        </div>
      </div>
    </div>
  );
}
