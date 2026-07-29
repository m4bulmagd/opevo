import Link from "next/link";

import { AgentRuntimeCard } from "@/components/agent/agent-runtime-card";
import { type AgentConfigurationTab, AgentSettingsForm } from "@/components/agent/agent-settings-form";
import { AssistantPreview } from "@/components/agent/assistant-preview";
import { ClerkSetupNotice } from "@/components/auth/clerk-setup-notice";
import { CapabilityBadge } from "@/components/product/capability-badge";
import { PageIntro } from "@/components/product/page-intro";
import { getAccount } from "@/lib/api/account";
import { getAgentConfigForRequest } from "@/lib/api/request-data";
import { isAppAuthConfigured } from "@/lib/auth/clerk-config";
import { cn } from "@/lib/utils";
import { normalizeAgentName } from "@/navigation/dashboard-items";

type AgentPageProps = {
  searchParams?: Promise<{ tab?: string | string[] }>;
};

type AgentPageTab = AgentConfigurationTab | "preview";

const TABS: ReadonlyArray<{ label: string; preview?: boolean; value: AgentPageTab }> = [
  { label: "General", value: "general" },
  { label: "Instructions", value: "instructions" },
  { label: "Knowledge", value: "knowledge" },
  { label: "Advanced", preview: true, value: "preview" },
];

function parseTab(value: string | string[] | undefined): AgentPageTab {
  const selected = Array.isArray(value) ? value[0] : value;
  return selected === "instructions" || selected === "knowledge" || selected === "preview" ? selected : "general";
}

function tabHref(tab: AgentPageTab): string {
  return tab === "general" ? "/dashboard/agent" : `/dashboard/agent?tab=${tab}`;
}

export default async function AgentPage({ searchParams = Promise.resolve({}) }: AgentPageProps = {}) {
  if (!isAppAuthConfigured) {
    return (
      <ClerkSetupNotice
        title="Agent settings are unavailable"
        description="Configure Clerk in your local environment before loading agent configuration."
      />
    );
  }

  const [{ tab }, agentConfig, account] = await Promise.all([searchParams, getAgentConfigForRequest(), getAccount()]);
  const activeTab = parseTab(tab);
  const agentName = normalizeAgentName(agentConfig.agent_name);

  return (
    <div className="@container/main flex flex-col gap-5">
      <PageIntro
        description="Shape how your receptionist identifies itself, follows instructions, and answers with business knowledge."
        dynamicContext
        eyebrow={agentName}
        title="Assistant"
      />
      <AgentRuntimeCard account={account} agentName={agentName} isEnabled={agentConfig.is_enabled} />
      <nav aria-label="Assistant sections" className="border-border border-b sm:overflow-x-auto">
        <div
          aria-label="Assistant configuration"
          className="grid grid-cols-2 gap-1 sm:flex sm:min-w-max"
          role="tablist"
        >
          {TABS.map((item) => {
            const active = item.value === activeTab;
            return (
              <Link
                aria-controls={`agent-panel-${item.value}`}
                aria-label={item.preview ? `${item.label} Preview` : undefined}
                aria-selected={active}
                className={cn(
                  "relative inline-flex min-h-11 items-center justify-center px-2 font-medium text-sm transition-colors sm:justify-start sm:px-3",
                  active ? "text-text-primary" : "text-text-secondary hover:text-text-primary",
                  active &&
                    "after:absolute after:inset-x-3 after:bottom-0 after:h-0.5 after:rounded-full after:bg-primary",
                )}
                href={tabHref(item.value)}
                id={`agent-tab-${item.value}`}
                key={item.value}
                role="tab"
              >
                <span>{item.label}</span>
                {item.preview ? <CapabilityBadge className="ml-2" status="preview" /> : null}
              </Link>
            );
          })}
        </div>
      </nav>
      {activeTab === "preview" ? (
        <div aria-labelledby="agent-tab-preview" id="agent-panel-preview" role="tabpanel">
          <AssistantPreview agentName={agentName} />
        </div>
      ) : (
        <AgentSettingsForm initialConfig={agentConfig} readOnly={account.status !== "active"} tab={activeTab} />
      )}
    </div>
  );
}
