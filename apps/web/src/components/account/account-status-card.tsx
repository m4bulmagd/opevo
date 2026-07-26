import Link from "next/link";

import { ReactivateAccountButton } from "@/components/account/reactivate-account-button";
import { StatusSurface, type StatusSurfaceTone } from "@/components/product/status-surface";
import { Button } from "@/components/ui/button";
import type { AccountStatus } from "@/lib/types/account";

const DEACTIVATION_PROGRESS_COPY: Record<NonNullable<AccountStatus["deactivation"]>["state"], string> = {
  requested: "Request accepted",
  disabling_routing: "Stopping new calls",
  canceling_subscription: "Canceling subscription",
  draining_call: "Waiting for an active call to finish",
  releasing_number: "Releasing your Presvo number",
  finalizing: "Finalizing your account",
  attention_required: "Cleanup needs additional time",
};

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
      description: "Presvo is no longer accepting new calls",
      progress,
    };
  }

  if (account.status === "active") {
    if (account.serving) {
      return {
        label: "Active",
        tone: "live",
        title: "Presvo is active",
        description: "Presvo can accept new calls.",
        progress: null,
      };
    }

    return {
      label: "Action needed",
      tone: "warning",
      title: "Presvo needs account attention",
      description: "Presvo is not accepting new calls yet.",
      progress: null,
    };
  }

  if (account.status === "deactivating") {
    return {
      label: "Deactivating",
      tone: "processing",
      title: "Finishing account deactivation",
      description: "Presvo is no longer accepting new calls",
      progress,
    };
  }

  return {
    label: "Inactive",
    tone: "inactive",
    title: "Presvo is inactive",
    description:
      "Your calls, recordings, billing history, and saved configuration remain available. Reactivation starts a new subscription and requires a newly provisioned number.",
    progress,
  };
}

export function AccountStatusCard({ account }: { account: AccountStatus }) {
  const lifecycle = getAccountLifecyclePresentation(account);
  const action =
    account.status === "inactive" && lifecycle.label !== "Attention required" ? (
      <div className="flex max-w-sm flex-col items-start gap-2" data-slot="reactivation-action">
        <ReactivateAccountButton reactivationAllowed={account.reactivation_allowed} />
      </div>
    ) : account.status === "active" && !account.serving ? (
      <Button asChild className="min-h-11" variant="outline">
        <Link href="/dashboard">Review Overview</Link>
      </Button>
    ) : undefined;

  return (
    <StatusSurface
      action={action}
      description={lifecycle.description}
      label={lifecycle.label}
      title={lifecycle.title}
      tone={lifecycle.tone}
    >
      {lifecycle.label === "Attention required" ? (
        <div className="flex flex-col gap-3">
          <p>Your retained data remains available while account cleanup finishes.</p>
          <p className="font-medium text-text-primary">
            Refresh this page. If cleanup still needs attention, contact Presvo support.
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
    </StatusSurface>
  );
}
