import { AgentRuntimeCard } from "@/components/agent/agent-runtime-card";
import { AgentSettingsForm } from "@/components/agent/agent-settings-form";
import { ClerkSetupNotice } from "@/components/auth/clerk-setup-notice";
import { getAgentConfig } from "@/lib/api/agent";
import { isClerkConfigured } from "@/lib/auth/clerk-config";
import type { AgentConfig } from "@/lib/types/agent";

const DEFAULT_AGENT_CONFIG: AgentConfig = {
  agent_name: "",
  owner_context: null,
  system_prompt: "",
  knowledge_base: "",
  pipeline_mode: "stt_llm_tts",
  is_enabled: false,
};

export default async function AgentPage() {
  if (!isClerkConfigured) {
    return (
      <ClerkSetupNotice
        title="Agent settings are unavailable"
        description="Configure Clerk in your local environment before loading agent configuration."
      />
    );
  }

  const agentConfig = (await getAgentConfig()) ?? DEFAULT_AGENT_CONFIG;

  return (
    <div className="@container/main grid gap-4 md:gap-6 lg:grid-cols-[minmax(0,1.45fr)_minmax(320px,1fr)]">
      <AgentSettingsForm initialConfig={agentConfig} />
      <aside className="flex flex-col gap-4 md:gap-6">
        <AgentRuntimeCard agentConfig={agentConfig} />
      </aside>
    </div>
  );
}
