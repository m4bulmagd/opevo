import { backendFetch } from "@/lib/api/backend-client";
import type {
  CallDetail,
  CallHistoryDateRange,
  CallHistoryListResponse,
  CallHistoryStatusFilter,
} from "@/lib/types/calls";

export type ListCallsOptions = {
  limit?: number;
  offset?: number;
  query?: string;
  status?: CallHistoryStatusFilter;
  range?: CallHistoryDateRange;
};

export async function listCalls({
  limit = 20,
  offset = 0,
  query,
  status = "all",
  range = "all",
}: ListCallsOptions = {}): Promise<CallHistoryListResponse> {
  const params = new URLSearchParams({
    limit: String(limit),
    offset: String(offset),
  });
  const normalizedQuery = query?.trim();
  if (normalizedQuery) {
    params.set("q", normalizedQuery);
  }
  if (status !== "all") {
    params.set("status", status);
  }
  if (range !== "all") {
    params.set("range", range);
  }
  return backendFetch<CallHistoryListResponse>(`/api/calls?${params.toString()}`);
}

export async function getCallDetail(callId: string) {
  return backendFetch<CallDetail>(`/api/calls/${callId}`);
}

export async function deleteCall(callId: string) {
  return backendFetch<void>(`/api/calls/${callId}`, {
    method: "DELETE",
  });
}
