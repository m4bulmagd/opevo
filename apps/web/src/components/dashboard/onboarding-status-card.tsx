"use client";

import { useState, useTransition } from "react";

import Link from "next/link";

import { AlertCircle, CheckCircle2, LoaderCircle, PhoneCall, RefreshCw, ShieldCheck } from "lucide-react";
import { toast } from "sonner";

import { type RetryProvisioningActionResult, retryProvisioningAction } from "@/app/(app)/dashboard/onboarding-actions";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Spinner } from "@/components/ui/spinner";
import type { OnboardingStatus } from "@/lib/types/onboarding";

type OnboardingStatusCardProps = {
  onboardingStatus: OnboardingStatus;
  retryHandler?: () => Promise<RetryProvisioningActionResult>;
};

function getStatusPresentation(onboardingStatus: OnboardingStatus) {
  switch (onboardingStatus.overall_status) {
    case "provisioning_number":
      return {
        title: "Number provisioning in progress",
        description: "We’re assigning your French number now. Check back shortly before enabling routing.",
        badgeLabel: "Provisioning",
        badgeVariant: "secondary" as const,
        icon: LoaderCircle,
      };
    case "provisioning_failed":
      return {
        title: "Provisioning needs attention",
        description: "Your subscription is active, but we could not finish assigning a number yet.",
        badgeLabel: "Action needed",
        badgeVariant: "secondary" as const,
        icon: AlertCircle,
      };
    case "ready_to_enable":
      return {
        title: "Ready to enable routing",
        description:
          "Your number is assigned and your setup is complete. Enable routing when you are ready to take live calls.",
        badgeLabel: "Ready",
        badgeVariant: "default" as const,
        icon: CheckCircle2,
      };
    case "live":
      return {
        title: "Routing is live",
        description: "Your number is active and inbound calls should route through the agent now.",
        badgeLabel: "Live",
        badgeVariant: "default" as const,
        icon: ShieldCheck,
      };
    case "setup_required":
      return {
        title: "Complete your agent setup",
        description: "Your number is ready. Finish the agent details before enabling routing.",
        badgeLabel: "Setup required",
        badgeVariant: "secondary" as const,
        icon: PhoneCall,
      };
    case "subscription_active":
      return {
        title: "Subscription active",
        description: "Your plan is active. The next step is automatic number provisioning.",
        badgeLabel: "Subscribed",
        badgeVariant: "secondary" as const,
        icon: PhoneCall,
      };
    default:
      return {
        title: "Start your setup",
        description: "Subscribe to the starter plan to begin automatic French number provisioning.",
        badgeLabel: "Not subscribed",
        badgeVariant: "secondary" as const,
        icon: PhoneCall,
      };
  }
}

export function OnboardingStatusCard({ onboardingStatus, retryHandler }: OnboardingStatusCardProps) {
  const { title, description, badgeLabel, badgeVariant, icon: Icon } = getStatusPresentation(onboardingStatus);
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
        {onboardingStatus.overall_status === "provisioning_failed" ? (
          <div className="rounded-lg border border-border/70 bg-muted/20 px-4 py-3 text-muted-foreground text-sm sm:col-span-2">
            Contact support if this keeps happening.
          </div>
        ) : null}
      </CardContent>
      <CardFooter className="flex flex-wrap gap-2">
        <Button asChild variant="secondary">
          <Link href="/dashboard/agent">Open agent settings</Link>
        </Button>
        {onboardingStatus.can_retry_provisioning ? (
          <Button onClick={onRetry} disabled={isPending}>
            {isPending ? <Spinner data-icon="inline-start" /> : null}
            {!isPending ? <RefreshCw className="size-4" /> : null}
            Retry provisioning
          </Button>
        ) : null}
        {feedback ? <p className="text-muted-foreground text-sm">{feedback}</p> : null}
      </CardFooter>
    </Card>
  );
}
