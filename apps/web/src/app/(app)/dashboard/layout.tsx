import type { ReactNode } from "react";

import { ClerkSetupNotice } from "@/components/auth/clerk-setup-notice";
import { resolveWorkspaceAccountControl } from "@/components/workspace/workspace-header";
import { WorkspaceShell } from "@/components/workspace/workspace-shell";
import { getAccount } from "@/lib/api/account";
import { getAgentConfigForRequest } from "@/lib/api/request-data";
import { isAppAuthConfigured } from "@/lib/auth/clerk-config";
import { normalizeAgentName } from "@/navigation/dashboard-items";

export default async function AppLayout({ children }: Readonly<{ children: ReactNode }>) {
  if (!isAppAuthConfigured) {
    return (
      <ClerkSetupNotice
        title="Authentication is not configured"
        description="Add your Clerk keys to enable protected dashboard routes and backend data."
      />
    );
  }

  const [account, agentConfig, accountControl] = await Promise.all([
    getAccount(),
    getAgentConfigForRequest(),
    resolveWorkspaceAccountControl(),
  ]);
  const agentName = normalizeAgentName(agentConfig.agent_name);

  return (
    <WorkspaceShell
      account={account}
      accountControl={accountControl}
      agentEnabled={agentConfig.is_enabled}
      agentName={agentName}
    >
      {children}
    </WorkspaceShell>
  );
}
