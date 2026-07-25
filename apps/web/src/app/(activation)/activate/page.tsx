import { redirect } from "next/navigation";

import { Badge } from "@/components/ui/badge";
import { getAccount } from "@/lib/api/account";
import { getActivationSnapshot } from "@/lib/api/activation";
import { getDevelopmentCapabilities } from "@/lib/development/capabilities";

import { ActivationShell } from "./_components/activation-shell";
import { ForwardingMilestone } from "./_components/forwarding/forwarding-milestone";
import { LaunchMilestone } from "./_components/launch/launch-milestone";
import { NumberMilestone } from "./_components/number/number-milestone";
import { ProfileForm } from "./_components/profile/profile-form";
import { type ActivationMilestoneId, canEnterDashboard, selectMilestone } from "./_components/stage-router";

type ActivationPageProps = {
  searchParams: Promise<{ milestone?: string | string[] }>;
};

const MILESTONE_COPY: Record<ActivationMilestoneId, { eyebrow: string; title: string; description: string }> = {
  business: {
    eyebrow: "Your business",
    title: "Tell us about your business",
    description: "Share the essentials Presvo needs to answer missed calls with the right context.",
  },
  receptionist: {
    eyebrow: "Your receptionist",
    title: "Shape your receptionist",
    description: "Set the voice, knowledge, and boundaries clients should hear when you cannot answer.",
  },
  number: {
    eyebrow: "Your Presvo line",
    title: "Choose your Presvo number",
    description: "Activate your plan, then review and approve your French number before anything is ordered.",
  },
  forwarding: {
    eyebrow: "Missed-call routing",
    title: "Forward missed calls to Presvo",
    description: "Follow the instructions for your carrier while keeping answered calls on your existing line.",
  },
  launch: {
    eyebrow: "Final checks",
    title: "Prepare to go live",
    description: "Verify forwarding, review readiness, and choose when your receptionist starts answering.",
  },
};

export default async function ActivationPage({ searchParams }: ActivationPageProps) {
  const [requested, account, snapshot, capabilities] = await Promise.all([
    searchParams,
    getAccount(),
    getActivationSnapshot(),
    getDevelopmentCapabilities(),
  ]);
  const requestedMilestone = Array.isArray(requested.milestone) ? requested.milestone[0] : requested.milestone;

  if (account.status !== "active") {
    redirect("/dashboard/account");
  }

  if (canEnterDashboard(snapshot)) {
    redirect("/dashboard");
  }

  const selectedMilestone = selectMilestone(snapshot, requestedMilestone ?? null);
  const copy = MILESTONE_COPY[selectedMilestone];
  const localJourney = capabilities.localBilling || capabilities.localVerification;

  return (
    <ActivationShell snapshot={snapshot} selectedMilestone={selectedMilestone}>
      <div className="flex flex-col gap-4">
        <div className="flex flex-wrap items-center gap-2">
          <p className="font-medium text-primary text-sm">{copy.eyebrow}</p>
          {localJourney ? <Badge variant="secondary">Local development</Badge> : null}
        </div>
        <div className="flex flex-col gap-3">
          <h1 id={`${selectedMilestone}-title`} className="max-w-2xl font-semibold text-3xl tracking-tight sm:text-4xl">
            {copy.title}
          </h1>
          <p className="max-w-2xl text-base text-muted-foreground leading-7">{copy.description}</p>
        </div>
        {selectedMilestone === "business" || selectedMilestone === "receptionist" ? (
          <ProfileForm snapshot={snapshot} milestone={selectedMilestone} />
        ) : null}
        {selectedMilestone === "number" ? (
          <NumberMilestone snapshot={snapshot} localBilling={capabilities.localBilling} />
        ) : null}
        {selectedMilestone === "forwarding" ? <ForwardingMilestone snapshot={snapshot} /> : null}
        {selectedMilestone === "launch" ? (
          <LaunchMilestone snapshot={snapshot} localVerification={capabilities.localVerification} />
        ) : null}
      </div>
    </ActivationShell>
  );
}
