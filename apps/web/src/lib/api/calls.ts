import { backendFetch } from "@/lib/api/backend-client";
import type { CallDetail, CallHistoryListResponse } from "@/lib/types/calls";

export async function listCalls(limit = 20, offset = 0) {
  const params = new URLSearchParams({
    limit: String(limit),
    offset: String(offset),
  });
  const response = await backendFetch<CallHistoryListResponse>(`/api/calls?${params.toString()}`);
  return response.calls;
}

export async function getCallDetail(callId: string) {
  return backendFetch<CallDetail>(`/api/calls/${callId}`);
}

export async function deleteCall(callId: string) {
  return backendFetch<void>(`/api/calls/${callId}`, {
    method: "DELETE",
  });
}
