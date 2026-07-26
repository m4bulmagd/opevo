import { CircleAlert, CircleCheck, CirclePause } from "lucide-react";

import { StatusSurface, type StatusSurfaceProps } from "@/components/product/status-surface";
import type { AccountStatus } from "@/lib/types/account";

type RuntimeStatus = Pick<StatusSurfaceProps, "description" | "icon" | "label" | "title" | "tone">;
type DeactivationState = NonNullable<AccountStatus["deactivation"]>["state"];

const DEACTIVATION_PROGRESS_COPY: Record<DeactivationState, string> = {
  requested: "Request accepted",
  disabling_routing: "Stopping new calls",
  canceling_subscription: "Canceling subscription",
  draining_call: "Waiting for an active call to finish",
  releasing_number: "Releasing your Presvo number",
  finalizing: "Finalizing your account",
  attention_required: "Cleanup needs attention",
};

function activeRuntimeStatus(account: AccountStatus, agentName: string, isEnabled: boolean): RuntimeStatus {
  if (!isEnabled) {
    return {
      description:
        "Call routing is disabled in the saved agent configuration. Presvo will evaluate serving readiness after you enable call routing.",
      icon: <CirclePause />,
      label: "Paused",
      title: `${agentName} is paused`,
      tone: "paused",
    };
  }

  if (!account.serving || account.blocker === "customer_not_ready") {
    return {
      description:
        "Call routing is enabled in the saved agent configuration. Account requirements do not currently permit serving. Review Overview for the next step.",
      icon: <CircleAlert />,
      label: "Action needed",
      title: `${agentName} needs account attention`,
      tone: "warning",
    };
  }

  return {
    description:
      "Call routing is enabled in the saved agent configuration. Account readiness currently permits Presvo to serve calls. This does not indicate that a call is in progress.",
    icon: <CircleCheck />,
    label: "Enabled",
    title: `${agentName} is enabled`,
    tone: "neutral",
  };
}

function deactivatingRuntimeStatus(account: AccountStatus): RuntimeStatus {
  const progress = account.deactivation
    ? DEACTIVATION_PROGRESS_COPY[account.deactivation.state]
    : "Cleanup in progress";
  const needsAttention =
    account.deactivation?.state === "attention_required" || account.blocker === "deactivation_attention_required";

  if (needsAttention) {
    return {
      description: `Saved agent settings are read-only. Presvo could not finish account cleanup. Current progress: ${progress}. Review the Account page for the latest state.`,
      icon: <CircleAlert />,
      label: "Attention required",
      title: "Account cleanup needs attention",
      tone: "attention",
    };
  }

  return {
    description: `Saved agent settings are read-only while Presvo finishes account deactivation. Current progress: ${progress}.`,
    icon: <CirclePause />,
    label: "Deactivating",
    title: "Account deactivation is in progress",
    tone: "warning",
  };
}

function inactiveRuntimeStatus(account: AccountStatus): RuntimeStatus {
  const withProgress = (description: string) =>
    account.deactivation
      ? `${description} Current progress: ${DEACTIVATION_PROGRESS_COPY[account.deactivation.state]}.`
      : description;

  if (account.blocker === "deactivation_attention_required") {
    return {
      description: withProgress(
        "Saved agent settings are read-only. Presvo could not finish account cleanup. Review the Account page for the latest state.",
      ),
      icon: <CircleAlert />,
      label: "Attention required",
      title: "Account cleanup needs attention",
      tone: "attention",
    };
  }

  if (account.blocker === "reactivation_not_ready") {
    return {
      description: withProgress(
        "Saved agent settings are read-only. Account readiness does not currently permit reactivation. Review the Account page for the latest state.",
      ),
      icon: <CircleAlert />,
      label: "Reactivation unavailable",
      title: "Reactivation is not ready",
      tone: "attention",
    };
  }

  return {
    description: withProgress(
      account.reactivation_allowed
        ? "Saved agent settings are read-only until you reactivate Presvo from the Account page."
        : "Saved agent settings are read-only. Reactivation is not currently available.",
    ),
    icon: <CirclePause />,
    label: "Inactive",
    title: "This agent configuration is read-only",
    tone: "inactive",
  };
}

function runtimeStatus({
  account,
  agentName,
  isEnabled,
}: {
  account: AccountStatus;
  agentName: string;
  isEnabled: boolean;
}): RuntimeStatus {
  if (account.status === "deactivating") {
    return deactivatingRuntimeStatus(account);
  }

  if (account.status === "inactive") {
    return inactiveRuntimeStatus(account);
  }

  return activeRuntimeStatus(account, agentName, isEnabled);
}

export function AgentRuntimeCard({
  account,
  agentName,
  isEnabled,
}: {
  account: AccountStatus;
  agentName: string;
  isEnabled: boolean;
}) {
  return <StatusSurface {...runtimeStatus({ account, agentName, isEnabled })} />;
}
