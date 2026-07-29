import type { ReactNode } from "react";

import { ClerkSetupNotice } from "@/components/auth/clerk-setup-notice";
import type { WorkspaceCallerIdentity } from "@/components/workspace/workspace-caller-status";
import { resolveWorkspaceAccountControl } from "@/components/workspace/workspace-header";
import { WorkspaceShell } from "@/components/workspace/workspace-shell";
import { getAccount } from "@/lib/api/account";
import { listCalls } from "@/lib/api/calls";
import { getAgentConfigForRequest } from "@/lib/api/request-data";
import { isAppAuthConfigured } from "@/lib/auth/clerk-config";
import { normalizeAgentName } from "@/navigation/dashboard-items";

async function resolveActiveCaller(): Promise<WorkspaceCallerIdentity | null> {
  try {
    const result = await listCalls({ limit: 1, status: "in_progress" });
    const call = result.calls[0];
    return call
      ? {
          contactName: null,
          phoneNumber: call.caller_number,
        }
      : null;
  } catch {
    return null;
  }
}

export default async function AppLayout({ children }: Readonly<{ children: ReactNode }>) {
  if (!isAppAuthConfigured) {
    return (
      <ClerkSetupNotice
        title="Authentication is not configured"
        description="Add your Clerk keys to enable protected dashboard routes and backend data."
      />
    );
  }

  const [account, agentConfig, accountControl, activeCaller] = await Promise.all([
    getAccount(),
    getAgentConfigForRequest(),
    resolveWorkspaceAccountControl(),
    resolveActiveCaller(),
  ]);
  const agentName = normalizeAgentName(agentConfig.agent_name);

  return (
    <WorkspaceShell
      account={account}
      accountControl={accountControl}
      activeCaller={activeCaller}
      agentEnabled={agentConfig.is_enabled}
      agentName={agentName}
    >
      {children}
    </WorkspaceShell>
  );
}
