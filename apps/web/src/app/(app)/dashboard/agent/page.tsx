import { AgentRuntimeCard } from "@/components/agent/agent-runtime-card";
import { AgentSettingsForm } from "@/components/agent/agent-settings-form";
import { ClerkSetupNotice } from "@/components/auth/clerk-setup-notice";
import { getAgentConfig } from "@/lib/api/agent";
import { isAppAuthConfigured } from "@/lib/auth/clerk-config";

export default async function AgentPage() {
  if (!isAppAuthConfigured) {
    return (
      <ClerkSetupNotice
        title="Agent settings are unavailable"
        description="Configure Clerk in your local environment before loading agent configuration."
      />
    );
  }

  const agentConfig = await getAgentConfig();

  return (
    <div className="@container/main grid gap-4 md:gap-6 lg:grid-cols-[minmax(0,1.45fr)_minmax(320px,1fr)]">
      <AgentSettingsForm initialConfig={agentConfig} />
      <aside className="flex flex-col gap-4 md:gap-6">
        <AgentRuntimeCard agentConfig={agentConfig} />
      </aside>
    </div>
  );
}
