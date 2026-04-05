"use server";

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

    return {
      status: "success",
      message: "Agent settings saved.",
      config,
    };
  } catch (error) {
    if (error instanceof BackendApiError) {
      if (error.status === 409) {
        return {
          status: "error",
          message: "Phone number not found. Assign a number before enabling routing.",
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
        message: error.message,
      };
    }

    return {
      status: "error",
      message: "Unexpected error while saving agent settings.",
    };
  }
}
