import { ClerkSetupNotice } from "@/components/auth/clerk-setup-notice";
import { AgentSnapshotCard } from "@/components/dashboard/agent-snapshot-card";
import { RecentCallsList } from "@/components/dashboard/recent-calls-list";
import { SetupChecklist } from "@/components/dashboard/setup-checklist";
import { StatusSummaryCards } from "@/components/dashboard/status-summary-cards";
import { UsageSummaryCard } from "@/components/dashboard/usage-summary-card";
import { getAgentConfig } from "@/lib/api/agent";
import { getUsageSnapshot } from "@/lib/api/billing";
import { listCalls } from "@/lib/api/calls";
import { isClerkConfigured } from "@/lib/auth/clerk-config";

export default async function DashboardPage() {
  if (!isClerkConfigured) {
    return (
      <ClerkSetupNotice
        title="Dashboard is unavailable"
        description="Add your Clerk keys to load customer data, call history, and billing state."
      />
    );
  }

  const [agentConfig, calls, usageSnapshot] = await Promise.all([getAgentConfig(), listCalls(5), getUsageSnapshot()]);

  const isLive = Boolean(agentConfig?.is_enabled);

  return (
    <div className="@container/main flex flex-col gap-4 md:gap-6">
      <StatusSummaryCards agentConfig={agentConfig} calls={calls} usageSnapshot={usageSnapshot} />
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1.7fr)_minmax(280px,1fr)] lg:gap-6">
        <section className="flex flex-col gap-4">
          {isLive ? (
            <RecentCallsList calls={calls} />
          ) : (
            <>
              <SetupChecklist agentConfig={agentConfig} />
              <RecentCallsList calls={calls} />
            </>
          )}
        </section>
        <aside className="flex flex-col gap-4">
          <AgentSnapshotCard agentConfig={agentConfig} />
          <UsageSummaryCard usageSnapshot={usageSnapshot} />
        </aside>
      </div>
    </div>
  );
}
