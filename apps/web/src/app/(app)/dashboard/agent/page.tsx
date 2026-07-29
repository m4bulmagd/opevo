import Link from "next/link";

import { AgentRuntimeCard } from "@/components/agent/agent-runtime-card";
import { type AgentConfigurationTab, AgentSettingsForm } from "@/components/agent/agent-settings-form";
import { ClerkSetupNotice } from "@/components/auth/clerk-setup-notice";
import { PageIntro } from "@/components/product/page-intro";
import { getAccount } from "@/lib/api/account";
import { getAgentConfigForRequest } from "@/lib/api/request-data";
import { isAppAuthConfigured } from "@/lib/auth/clerk-config";
import { cn } from "@/lib/utils";
import { normalizeAgentName } from "@/navigation/dashboard-items";

type AgentPageProps = {
  searchParams?: Promise<{ tab?: string | string[] }>;
};

const TABS: ReadonlyArray<{ label: string; value: AgentConfigurationTab }> = [
  { label: "General", value: "general" },
  { label: "Instructions", value: "instructions" },
  { label: "Knowledge", value: "knowledge" },
];

function parseTab(value: string | string[] | undefined): AgentConfigurationTab {
  const selected = Array.isArray(value) ? value[0] : value;
  return selected === "instructions" || selected === "knowledge" ? selected : "general";
}

function tabHref(tab: AgentConfigurationTab): string {
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
      <nav aria-label="Assistant sections" className="overflow-x-auto border-border border-b">
        <div aria-label="Assistant configuration" className="flex min-w-max gap-1" role="tablist">
          {TABS.map((item) => {
            const active = item.value === activeTab;
            return (
              <Link
                aria-controls={`agent-panel-${item.value}`}
                aria-selected={active}
                className={cn(
                  "relative inline-flex min-h-11 items-center px-3 font-medium text-sm transition-colors",
                  active ? "text-text-primary" : "text-text-secondary hover:text-text-primary",
                  active &&
                    "after:absolute after:inset-x-3 after:bottom-0 after:h-0.5 after:rounded-full after:bg-primary",
                )}
                href={tabHref(item.value)}
                id={`agent-tab-${item.value}`}
                key={item.value}
                role="tab"
              >
                {item.label}
              </Link>
            );
          })}
        </div>
      </nav>
      <AgentSettingsForm initialConfig={agentConfig} readOnly={account.status !== "active"} tab={activeTab} />
    </div>
  );
}
