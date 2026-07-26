import { redirect } from "next/navigation";

import { canEnterDashboard } from "@/app/(activation)/activate/_components/stage-router";
import { ClerkSetupNotice } from "@/components/auth/clerk-setup-notice";
import { AnsweringStatusBanner } from "@/components/dashboard/answering-status-banner";
import { AttentionSurface } from "@/components/dashboard/attention-surface";
import { DashboardCallLedger } from "@/components/dashboard/dashboard-call-ledger";
import { DashboardMetricsBand } from "@/components/dashboard/dashboard-metrics";
import { OnboardingStatusCard } from "@/components/dashboard/onboarding-status-card";
import { PlanUsageSurface } from "@/components/dashboard/plan-usage-surface";
import { SetupChecklist } from "@/components/dashboard/setup-checklist";
import { PageIntro } from "@/components/product/page-intro";
import { getActivationSnapshot } from "@/lib/api/activation";
import { getUsageSnapshot } from "@/lib/api/billing";
import { listCalls } from "@/lib/api/calls";
import { getDashboardMetrics } from "@/lib/api/dashboard";
import { getOnboardingStatus } from "@/lib/api/onboarding";
import { getAgentConfigForRequest } from "@/lib/api/request-data";
import { isAppAuthConfigured } from "@/lib/auth/clerk-config";
import { normalizeAgentName } from "@/navigation/dashboard-items";

function dashboardDateContext(timezone: string) {
  return `${new Intl.DateTimeFormat("en-US", {
    day: "numeric",
    month: "long",
    timeZone: timezone,
    weekday: "long",
  }).format(new Date())} · ${timezone}`;
}

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

  const metricsPromise = getDashboardMetrics()
    .then((value) => ({ status: "ready" as const, value }))
    .catch(() => ({ status: "unavailable" as const }));

  const [agentConfig, onboardingStatus, callsPage, usageSnapshot, metricsResult] = await Promise.all([
    getAgentConfigForRequest(),
    getOnboardingStatus(),
    listCalls({ limit: 5 }),
    getUsageSnapshot(),
    metricsPromise,
  ]);

  const calls = callsPage.calls;
  const isLive = onboardingStatus.can_route;
  const agentName = normalizeAgentName(agentConfig.agent_name);
  const metrics = metricsResult.status === "ready" ? metricsResult.value : null;

  return (
    <div className="@container/main flex flex-col gap-6 lg:gap-8">
      <PageIntro
        description="A clear view of call activity, follow-up signals, and plan health."
        dynamicContext
        eyebrow={
          <span data-visual-dynamic="true">
            {metrics ? dashboardDateContext(metrics.timezone) : "Date context unavailable"}
          </span>
        }
        title="Operations overview"
      />
      <AnsweringStatusBanner agentName={agentName} onboardingStatus={onboardingStatus} />
      {!isLive ? (
        <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_minmax(320px,0.72fr)]">
          <OnboardingStatusCard onboardingStatus={onboardingStatus} />
          <SetupChecklist agentConfig={agentConfig} onboardingStatus={onboardingStatus} />
        </div>
      ) : null}
      <DashboardMetricsBand metrics={metrics} usageSnapshot={usageSnapshot} />
      <DashboardCallLedger calls={calls} />
      <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,1.25fr)_minmax(320px,0.75fr)]">
        <AttentionSurface calls={calls} />
        <PlanUsageSurface usageSnapshot={usageSnapshot} />
      </div>
    </div>
  );
}
