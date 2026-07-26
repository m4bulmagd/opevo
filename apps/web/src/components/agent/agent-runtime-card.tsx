import { CircleAlert, CircleCheck, CirclePause } from "lucide-react";

import { StatusSurface, type StatusSurfaceProps } from "@/components/product/status-surface";
import type { AccountStatus } from "@/lib/types/account";

type RuntimeStatus = Pick<StatusSurfaceProps, "description" | "icon" | "label" | "title" | "tone">;

function runtimeStatus({
  accountStatus,
  agentName,
  isEnabled,
}: {
  accountStatus: AccountStatus["status"];
  agentName: string;
  isEnabled: boolean;
}): RuntimeStatus {
  if (accountStatus === "deactivating") {
    return {
      description: "Saved agent settings are read-only while Presvo finishes account deactivation.",
      icon: <CirclePause />,
      label: "Deactivating",
      title: "Account deactivation is in progress",
      tone: "warning",
    };
  }

  if (accountStatus === "inactive") {
    return {
      description: "Saved agent settings are read-only until Presvo is reactivated.",
      icon: <CircleAlert />,
      label: "Inactive",
      title: "This agent configuration is read-only",
      tone: "inactive",
    };
  }

  if (isEnabled) {
    return {
      description:
        "Call routing is enabled in the saved agent configuration. Runtime availability also depends on account, number, and setup state.",
      icon: <CircleCheck />,
      label: "Enabled",
      title: `${agentName} is enabled`,
      tone: "neutral",
    };
  }

  return {
    description: "Call routing is disabled in the saved agent configuration. Settings remain available to edit.",
    icon: <CirclePause />,
    label: "Paused",
    title: `${agentName} is paused`,
    tone: "paused",
  };
}

export function AgentRuntimeCard({
  accountStatus,
  agentName,
  isEnabled,
}: {
  accountStatus: AccountStatus["status"];
  agentName: string;
  isEnabled: boolean;
}) {
  return <StatusSurface {...runtimeStatus({ accountStatus, agentName, isEnabled })} />;
}
