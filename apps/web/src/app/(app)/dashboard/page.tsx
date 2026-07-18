import { redirect } from "next/navigation";

import { canEnterDashboard } from "@/app/(activation)/activate/_components/stage-router";
import { ClerkSetupNotice } from "@/components/auth/clerk-setup-notice";
import { AgentSnapshotCard } from "@/components/dashboard/agent-snapshot-card";
import { OnboardingStatusCard } from "@/components/dashboard/onboarding-status-card";
import { RecentCallsList } from "@/components/dashboard/recent-calls-list";
import { SetupChecklist } from "@/components/dashboard/setup-checklist";
import { StatusSummaryCards } from "@/components/dashboard/status-summary-cards";
import { UsageSummaryCard } from "@/components/dashboard/usage-summary-card";
import { getActivationSnapshot } from "@/lib/api/activation";
import { getAgentConfig } from "@/lib/api/agent";
import { getUsageSnapshot } from "@/lib/api/billing";
import { listCalls } from "@/lib/api/calls";
import { getOnboardingStatus } from "@/lib/api/onboarding";
import { isAppAuthConfigured } from "@/lib/auth/clerk-config";

export default async function DashboardPage() {
  if (!isAppAuthConfigured) {
    return (
      <ClerkSetupNotice
        title="Dashboard is unavailable"
        description="Add your Clerk keys to load customer data, call history, and billing state."
      />
    );
  }

  const activation = await getActivationSnapshot();
  if (!canEnterDashboard(activation)) {
    redirect("/activate");
  }

  const [agentConfig, onboardingStatus, calls, usageSnapshot] = await Promise.all([
    getAgentConfig(),
    getOnboardingStatus(),
    listCalls(5),
    getUsageSnapshot(),
  ]);

  const isLive = onboardingStatus.can_route;

  return (
    <div className="@container/main flex flex-col gap-4 md:gap-6">
      <StatusSummaryCards
        agentConfig={agentConfig}
        onboardingStatus={onboardingStatus}
        calls={calls}
        usageSnapshot={usageSnapshot}
      />
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1.7fr)_minmax(280px,1fr)] lg:gap-6">
        <section className="flex flex-col gap-4">
          <OnboardingStatusCard onboardingStatus={onboardingStatus} />
          {isLive ? (
            <RecentCallsList calls={calls} />
          ) : (
            <>
              <SetupChecklist agentConfig={agentConfig} onboardingStatus={onboardingStatus} />
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
