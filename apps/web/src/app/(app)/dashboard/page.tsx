import Link from "next/link";
import { redirect } from "next/navigation";

import { PhoneForwarded, Settings2, Sparkles } from "lucide-react";

import { canEnterDashboard } from "@/app/(activation)/activate/_components/stage-router";
import { ClerkSetupNotice } from "@/components/auth/clerk-setup-notice";
import { ActivityChart } from "@/components/dashboard/activity-chart";
import { AnsweringStatusBanner } from "@/components/dashboard/answering-status-banner";
import { AttentionSurface } from "@/components/dashboard/attention-surface";
import { DashboardCallLedger } from "@/components/dashboard/dashboard-call-ledger";
import { DashboardMetricsBand } from "@/components/dashboard/dashboard-metrics";
import { OnboardingStatusCard } from "@/components/dashboard/onboarding-status-card";
import { PlanUsageSurface } from "@/components/dashboard/plan-usage-surface";
import { SetupChecklist } from "@/components/dashboard/setup-checklist";
import { PageIntro } from "@/components/product/page-intro";
import { ProductSurface } from "@/components/product/product-surface";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { getActivationSnapshot } from "@/lib/api/activation";
import { getUsageSnapshot } from "@/lib/api/billing";
import { listCalls } from "@/lib/api/calls";
import { getDashboardMetrics } from "@/lib/api/dashboard";
import { getOnboardingStatus } from "@/lib/api/onboarding";
import { getAgentConfigForRequest } from "@/lib/api/request-data";
import { isAppAuthConfigured } from "@/lib/auth/clerk-config";
import { formatPhoneNumber } from "@/lib/formatters";
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
    <div className="@container/main space-y-5">
      <PageIntro
        description="A clear view of call activity, follow-up signals, and plan health."
        dynamicContext
        eyebrow={
          <span data-visual-dynamic="true">
            {metrics ? dashboardDateContext(metrics.timezone) : "Date context unavailable"}
          </span>
        }
        action={
          <div className="flex flex-wrap gap-2">
            <Button asChild className="min-h-11" variant="outline">
              <Link href="/dashboard/agent">
                <Settings2 data-icon="inline-start" />
                Configure receptionist
              </Link>
            </Button>
            <Button asChild className="min-h-11">
              <Link href="/dashboard/billing">
                <Sparkles data-icon="inline-start" />
                Review billing
              </Link>
            </Button>
          </div>
        }
        title="Operations overview"
      />
      <DashboardMetricsBand metrics={metrics} />
      {!isLive ? (
        <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(320px,0.72fr)]">
          <OnboardingStatusCard onboardingStatus={onboardingStatus} />
          <AnsweringStatusBanner agentName={agentName} onboardingStatus={onboardingStatus} />
        </div>
      ) : null}
      <div className="grid items-start gap-5 lg:grid-cols-[minmax(0,2fr)_minmax(18rem,1fr)]">
        <section
          aria-labelledby="call-activity-heading"
          className="surface-card p-4 sm:p-5"
          data-slot="activity-surface"
        >
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <div>
              <h2 className="font-semibold text-sm" id="call-activity-heading">
                Call activity
              </h2>
              <p className="mt-1 text-text-secondary text-xs">Calls received over the last 7 local days.</p>
            </div>
            <span className="text-label">Last 7 days</span>
          </div>
          <div className="mt-4">
            {metrics ? (
              <ActivityChart data={metrics.daily_activity} />
            ) : (
              <div className="grid min-h-64 place-items-center rounded-xl border border-border border-dashed bg-muted/30 px-5 text-center text-sm text-text-secondary">
                Activity data is temporarily unavailable.
              </div>
            )}
          </div>
        </section>
        <div className="space-y-5">
          {isLive ? <AnsweringStatusBanner agentName={agentName} onboardingStatus={onboardingStatus} /> : null}
          <ProductSurface
            action={<Badge variant={onboardingStatus.phone_number ? "default" : "secondary"}>France · +33</Badge>}
            as="div"
            description="The Presvo line used for conditional forwarding."
            title={
              <span className="flex items-center gap-2">
                <PhoneForwarded aria-hidden className="size-4 text-text-tertiary" />
                Assigned number
              </span>
            }
          >
            <p className="truncate font-semibold text-xl tracking-tight">
              {formatPhoneNumber(onboardingStatus.phone_number)}
            </p>
          </ProductSurface>
        </div>
      </div>
      <div className="grid items-start gap-5 lg:grid-cols-2">
        <PlanUsageSurface usageSnapshot={usageSnapshot} />
        <SetupChecklist agentConfig={agentConfig} onboardingStatus={onboardingStatus} />
      </div>
      <DashboardCallLedger calls={calls} />
      <AttentionSurface calls={calls} />
    </div>
  );
}
