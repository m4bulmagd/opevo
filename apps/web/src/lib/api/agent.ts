import { backendFetch } from "@/lib/api/backend-client";
import type { AgentConfig, AgentConfigPatch } from "@/lib/types/agent";

export async function getAgentConfig() {
  return backendFetch<AgentConfig>("/api/agent/config");
}

export async function patchAgentConfig(payload: AgentConfigPatch) {
  return backendFetch<AgentConfig>("/api/agent/config", {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}
