"use client";

import { useState, useTransition } from "react";

import Link from "next/link";

import {
  AlertCircle,
  CheckCircle2,
  CirclePause,
  CreditCard,
  LoaderCircle,
  PhoneCall,
  RefreshCw,
  Settings2,
  ShieldCheck,
} from "lucide-react";
import { toast } from "sonner";

import { type RetryProvisioningActionResult, retryProvisioningAction } from "@/app/(app)/dashboard/onboarding-actions";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Spinner } from "@/components/ui/spinner";
import type { OnboardingStatus, ReadinessBlocker } from "@/lib/types/onboarding";

type OnboardingStatusCardProps = {
  onboardingStatus: OnboardingStatus;
  retryHandler?: () => Promise<RetryProvisioningActionResult>;
};

type StatusAction = "activation" | "billing" | null;

const SUBSCRIPTION_BLOCKERS = new Set<ReadinessBlocker>([
  "subscription_missing",
  "plan_unsupported",
  "subscription_status_ineligible",
  "subscription_period_missing",
  "subscription_period_inactive",
]);

function getSuspendedPresentation(onboardingStatus: OnboardingStatus) {
  if (onboardingStatus.blockers.includes("minutes_exhausted")) {
    return {
      title: "No minutes remaining",
      description: "Add minutes or restore your plan in billing before calls can be answered.",
      badgeLabel: "Paused",
      badgeVariant: "secondary" as const,
      icon: CirclePause,
      action: "billing" as const,
    };
  }

  if (onboardingStatus.blockers.some((blocker) => SUBSCRIPTION_BLOCKERS.has(blocker))) {
    return {
      title: "Subscription needs attention",
      description: "Review your subscription in billing before your receptionist can answer calls.",
      badgeLabel: "Paused",
      badgeVariant: "secondary" as const,
      icon: CreditCard,
      action: "billing" as const,
    };
  }

  return {
    title: "Your receptionist is safely offline",
    description: "Presvo found an account condition that needs attention before calls can go live.",
    badgeLabel: "Offline",
    badgeVariant: "secondary" as const,
    icon: CirclePause,
    action: null,
  };
}

function getStatusPresentation(onboardingStatus: OnboardingStatus) {
  switch (onboardingStatus.stage) {
    case "number_provisioning":
      return {
        title: "Number provisioning in progress",
        description: "We’re assigning your Presvo number now. This usually finishes automatically.",
        badgeLabel: "Provisioning",
        badgeVariant: "secondary" as const,
        icon: LoaderCircle,
        action: "activation" as const,
      };
    case "number_provisioning_failed":
      return {
        title: "Provisioning needs attention",
        description: "Retry provisioning. If the issue continues, your setup will remain safely offline.",
        badgeLabel: "Action needed",
        badgeVariant: "secondary" as const,
        icon: AlertCircle,
        action: null,
      };
    case "receptionist_setup_required":
      return {
        title: "Complete your receptionist setup",
        description: "Your number is ready. Add your business context and call-handling instructions next.",
        badgeLabel: "Setup required",
        badgeVariant: "secondary" as const,
        icon: Settings2,
        action: "activation" as const,
      };
    case "ready":
      return {
        title: "Ready to go live",
        description: "Your number and receptionist setup are complete. Turn it on when you’re ready.",
        badgeLabel: "Ready",
        badgeVariant: "default" as const,
        icon: CheckCircle2,
        action: "activation" as const,
      };
    case "routing_pending":
      return {
        title: "Routing update in progress",
        description: "Presvo is applying your routing update. Calls stay safely offline until every check passes.",
        badgeLabel: "Updating",
        badgeVariant: "secondary" as const,
        icon: LoaderCircle,
        action: null,
      };
    case "live":
      return {
        title: "Your receptionist is live",
        description: "Calls to your Presvo number can now be answered by your receptionist.",
        badgeLabel: "Live",
        badgeVariant: "default" as const,
        icon: ShieldCheck,
        action: "activation" as const,
      };
    case "subscription_required":
      return {
        title: "Choose your plan",
        description: "Activate the starter plan to begin Presvo number provisioning.",
        badgeLabel: "Plan required",
        badgeVariant: "secondary" as const,
        icon: CreditCard,
        action: "billing" as const,
      };
    case "suspended":
      return getSuspendedPresentation(onboardingStatus);
    default:
      return {
        title: "Your receptionist is safely offline",
        description: "Refresh your dashboard before enabling calls.",
        badgeLabel: "Offline",
        badgeVariant: "secondary" as const,
        icon: PhoneCall,
        action: null,
      };
  }
}

function StatusActionLink({ action }: { action: StatusAction }) {
  if (action === "billing") {
    return (
      <Button asChild>
        <Link href="/dashboard/billing">Manage billing</Link>
      </Button>
    );
  }

  if (action === "activation") {
    return (
      <Button asChild variant="secondary">
        <Link href="/activate">Review activation</Link>
      </Button>
    );
  }

  return null;
}

export function OnboardingStatusCard({ onboardingStatus, retryHandler }: OnboardingStatusCardProps) {
  const { title, description, badgeLabel, badgeVariant, icon: Icon, action } = getStatusPresentation(onboardingStatus);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [isPending, startTransition] = useTransition();

  const onRetry = () => {
    startTransition(async () => {
      const result = await (retryHandler ?? retryProvisioningAction)();
      setFeedback(result.message);

      if (result.status === "success") {
        toast.success(result.message);
        return;
      }

      toast.error(result.message);
    });
  };

  const hasFooter = action !== null || onboardingStatus.can_retry_provisioning || feedback !== null;

  return (
    <Card>
      <CardHeader className="gap-3">
        <div className="flex items-start justify-between gap-4">
          <div className="space-y-1">
            <CardTitle className="flex items-center gap-2">
              <Icon className="size-4 text-muted-foreground" />
              {title}
            </CardTitle>
            <CardDescription>{description}</CardDescription>
          </div>
          <Badge variant={badgeVariant}>{badgeLabel}</Badge>
        </div>
      </CardHeader>
      <CardContent className="grid gap-3 sm:grid-cols-2">
        <div className="rounded-lg border border-border/70 bg-muted/20 px-4 py-3">
          <div className="text-muted-foreground text-xs uppercase tracking-[0.14em]">Assigned number</div>
          <div className="mt-1 font-medium">
            {onboardingStatus.phone_number ?? "We’ll show your number here once provisioning succeeds."}
          </div>
        </div>
        <div className="rounded-lg border border-border/70 bg-muted/20 px-4 py-3">
          <div className="text-muted-foreground text-xs uppercase tracking-[0.14em]">Plan status</div>
          <div className="mt-1 font-medium">
            {onboardingStatus.plan_tier
              ? `${onboardingStatus.plan_tier} · ${onboardingStatus.subscription_status ?? "pending"}`
              : "No active plan"}
          </div>
        </div>
      </CardContent>
      {hasFooter ? (
        <CardFooter className="flex flex-wrap gap-2">
          <StatusActionLink action={action} />
          {onboardingStatus.can_retry_provisioning ? (
            <Button onClick={onRetry} disabled={isPending}>
              {isPending ? <Spinner data-icon="inline-start" /> : null}
              {!isPending ? <RefreshCw data-icon="inline-start" /> : null}
              Retry provisioning
            </Button>
          ) : null}
          {feedback ? (
            <p aria-live="polite" className="text-muted-foreground text-sm">
              {feedback}
            </p>
          ) : null}
        </CardFooter>
      ) : null}
    </Card>
  );
}
