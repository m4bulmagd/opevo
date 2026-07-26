import { AgentRuntimeCard } from "@/components/agent/agent-runtime-card";
import { AgentSettingsForm } from "@/components/agent/agent-settings-form";
import { ClerkSetupNotice } from "@/components/auth/clerk-setup-notice";
import { PageIntro } from "@/components/product/page-intro";
import { getAccount } from "@/lib/api/account";
import { getAgentConfigForRequest } from "@/lib/api/request-data";
import { isAppAuthConfigured } from "@/lib/auth/clerk-config";
import { normalizeAgentName } from "@/navigation/dashboard-items";

export default async function AgentPage() {
  if (!isAppAuthConfigured) {
    return (
      <ClerkSetupNotice
        title="Agent settings are unavailable"
        description="Configure Clerk in your local environment before loading agent configuration."
      />
    );
  }

  const [agentConfig, account] = await Promise.all([getAgentConfigForRequest(), getAccount()]);
  const agentName = normalizeAgentName(agentConfig.agent_name);

  return (
    <div className="@container/main flex flex-col gap-6 md:gap-8">
      <PageIntro
        description="Manage the identity, call handling, and operational knowledge saved for your receptionist."
        eyebrow="Agent configuration"
        title={agentName}
      />
      <AgentRuntimeCard account={account} agentName={agentName} isEnabled={agentConfig.is_enabled} />
      <AgentSettingsForm initialConfig={agentConfig} readOnly={account.status !== "active"} />
    </div>
  );
}
