"use server";

import { revalidatePath } from "next/cache";

import { patchAgentConfig } from "@/lib/api/agent";
import { BackendApiError } from "@/lib/api/backend-client";
import type { AgentConfig, AgentConfigPatch } from "@/lib/types/agent";

export type AgentActionResult = {
  status: "success" | "error";
  message: string;
  config?: AgentConfig;
};

export async function saveAgentSettingsAction(payload: AgentConfigPatch): Promise<AgentActionResult> {
  try {
    const config = await patchAgentConfig(payload);
    revalidatePath("/dashboard/agent");
    revalidatePath("/dashboard");

    return {
      status: "success",
      message: "Agent settings saved.",
      config,
    };
  } catch (error) {
    if (error instanceof BackendApiError) {
      const detail = typeof error.detail === "object" ? error.detail : null;
      if (detail?.code === "account_deactivating") {
        return {
          status: "error",
          message: "Agent settings are read-only while account deactivation is finishing.",
        };
      }

      if (detail?.code === "account_inactive") {
        return {
          status: "error",
          message: "Reactivate Presvo before changing agent settings.",
        };
      }

      if (error.status === 409) {
        return {
          status: "error",
          message: "Enable routing only after billing is active, your number is assigned, and setup is complete.",
        };
      }

      if (error.status === 502) {
        return {
          status: "error",
          message: "Failed to update telephony state. Try again in a moment.",
        };
      }

      return {
        status: "error",
        message: "We couldn't save agent settings. Refresh and try again.",
      };
    }

    return {
      status: "error",
      message: "Unexpected error while saving agent settings.",
    };
  }
}
