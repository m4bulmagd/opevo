import type { ReactNode } from "react";

import Link from "next/link";

import { ReactivateAccountButton } from "@/components/account/reactivate-account-button";
import { AnimatedStatusBadge } from "@/components/motion/animated-status-badge";
import { StatusSurface, type StatusSurfaceTone } from "@/components/product/status-surface";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardFooter, CardHeader } from "@/components/ui/card";
import type { AccountStatus } from "@/lib/types/account";

const DEACTIVATION_PROGRESS_COPY: Record<NonNullable<AccountStatus["deactivation"]>["state"], string> = {
  requested: "Request accepted",
  disabling_routing: "Stopping new calls",
  canceling_subscription: "Canceling subscription",
  draining_call: "Waiting for an active call to finish",
  releasing_number: "Releasing your Opevo number",
  finalizing: "Finalizing your account",
  attention_required: "Cleanup needs additional time",
};

const INACTIVE_CYCLE_COPY =
  "Your calls, recordings, billing history, and saved configuration remain available. Reactivation starts a new subscription and requires a newly provisioned number.";

type AccountLifecyclePresentation = {
  label: "Active" | "Action needed" | "Deactivating" | "Attention required" | "Inactive";
  tone: StatusSurfaceTone;
  title: string;
  description: string;
  progress: string | null;
};

function hasDeactivationAttention(account: AccountStatus) {
  return account.deactivation?.state === "attention_required" || account.blocker === "deactivation_attention_required";
}

export function getAccountLifecyclePresentation(account: AccountStatus): AccountLifecyclePresentation {
  const progress = account.deactivation ? DEACTIVATION_PROGRESS_COPY[account.deactivation.state] : null;

  if (hasDeactivationAttention(account)) {
    return {
      label: "Attention required",
      tone: "attention",
      title: "Account cleanup needs attention",
      description: account.status === "inactive" ? "Opevo is inactive" : "Opevo is no longer accepting new calls",
      progress,
    };
  }

  if (account.status === "active") {
    if (account.serving) {
      return {
        label: "Active",
        tone: "live",
        title: "Opevo is active",
        description: "Opevo can accept new calls.",
        progress: null,
      };
    }

    return {
      label: "Action needed",
      tone: "warning",
      title: "Opevo needs account attention",
      description: "Opevo is not accepting new calls yet.",
      progress: null,
    };
  }

  if (account.status === "deactivating") {
    return {
      label: "Deactivating",
      tone: "processing",
      title: "Finishing account deactivation",
      description: "Opevo is no longer accepting new calls",
      progress,
    };
  }

  return {
    label: "Inactive",
    tone: "inactive",
    title: "Opevo is inactive",
    description: INACTIVE_CYCLE_COPY,
    progress,
  };
}

function getAccountLifecycleAction(account: AccountStatus): ReactNode {
  if (account.status === "inactive") {
    return (
      <div className="flex max-w-sm flex-col items-start gap-2" data-slot="reactivation-action">
        <ReactivateAccountButton reactivationAllowed={account.reactivation_allowed} />
      </div>
    );
  }

  if (account.status === "active" && !account.serving) {
    return (
      <Button asChild className="min-h-11" variant="outline">
        <Link href="/dashboard">Review Overview</Link>
      </Button>
    );
  }

  return undefined;
}

function AccountLifecycleDetail({
  account,
  lifecycle,
}: Readonly<{ account: AccountStatus; lifecycle: AccountLifecyclePresentation }>) {
  return (
    <>
      {lifecycle.label === "Attention required" ? (
        <div className="flex flex-col gap-3">
          <p>
            {account.status === "inactive"
              ? INACTIVE_CYCLE_COPY
              : "Your retained data remains available while account cleanup finishes."}
          </p>
          <p className="font-medium text-text-primary">
            Refresh this page. If cleanup still needs attention, contact Opevo support.
          </p>
        </div>
      ) : null}

      {account.status === "deactivating" && lifecycle.label !== "Attention required" ? (
        <p>Your retained data remains available while account cleanup finishes.</p>
      ) : null}

      {lifecycle.progress ? (
        <div className="mt-4 flex flex-col gap-1 border-border/70 border-t pt-4">
          <span className="font-medium text-text-tertiary text-xs uppercase tracking-widest">Current progress</span>
          <span className="font-medium text-text-primary">{lifecycle.progress}</span>
        </div>
      ) : null}
    </>
  );
}

export function AccountStatusCard({ account }: Readonly<{ account: AccountStatus }>) {
  const lifecycle = getAccountLifecyclePresentation(account);
  const action = getAccountLifecycleAction(account);

  return (
    <StatusSurface
      action={action}
      description={lifecycle.description}
      label={lifecycle.label}
      title={lifecycle.title}
      tone={lifecycle.tone}
    >
      <AccountLifecycleDetail account={account} lifecycle={lifecycle} />
    </StatusSurface>
  );
}

export function CompactAccountStatusCard({ account }: Readonly<{ account: AccountStatus }>): ReactNode {
  const lifecycle = getAccountLifecyclePresentation(account);
  const action = getAccountLifecycleAction(account);

  return (
    <Card aria-label="Account status" role="region" size="sm">
      <CardHeader>
        <h2 className="font-medium text-base text-text-primary leading-normal">Account status</h2>
      </CardHeader>

      <CardContent className="flex flex-col gap-3">
        <AnimatedStatusBadge label={lifecycle.label} tone={lifecycle.tone} />
        <div className="flex flex-col gap-1.5">
          <p className="font-medium text-text-primary">{lifecycle.title}</p>
          <p className="text-sm text-text-secondary leading-relaxed">{lifecycle.description}</p>
        </div>
        <div className="text-sm text-text-secondary leading-relaxed">
          <AccountLifecycleDetail account={account} lifecycle={lifecycle} />
        </div>
      </CardContent>

      {action ? <CardFooter className="border-border/70 border-t pt-4">{action}</CardFooter> : null}
    </Card>
  );
}
