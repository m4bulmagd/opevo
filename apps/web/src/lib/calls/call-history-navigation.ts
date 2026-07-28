import type { CallHistoryDateRange, CallHistoryStatusFilter } from "@/lib/types/calls";

export const CALLS_PAGE_SIZE = 20 as const;

export type CallHistorySearchParams = {
  q?: string | string[];
  status?: string | string[];
  range?: string | string[];
  page?: string | string[];
};

export type CallHistoryNavigation = {
  query: string;
  status: CallHistoryStatusFilter;
  range: CallHistoryDateRange;
  page: number;
  limit: typeof CALLS_PAGE_SIZE;
  offset: number;
};

function firstValue(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}

function singleValue(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? undefined : value;
}

function parseStatus(value: string | undefined): CallHistoryStatusFilter {
  return value === "completed" || value === "in_progress" || value === "failed" ? value : "all";
}

function parseRange(value: string | undefined): CallHistoryDateRange {
  return value === "7d" || value === "30d" ? value : "all";
}

function parsePage(value: string | undefined): number {
  if (value === undefined || !/^[1-9]\d*$/.test(value)) {
    return 1;
  }
  const page = Number(value);
  if (!Number.isSafeInteger(page)) {
    return 1;
  }
  const offset = (page - 1) * CALLS_PAGE_SIZE;
  return Number.isSafeInteger(offset) ? page : 1;
}

export function parseCallHistoryNavigation(params: CallHistorySearchParams): CallHistoryNavigation {
  const query = firstValue(params.q)?.trim() ?? "";
  const page = parsePage(firstValue(params.page));
  return {
    query,
    status: parseStatus(singleValue(params.status)),
    range: parseRange(singleValue(params.range)),
    page,
    limit: CALLS_PAGE_SIZE,
    offset: (page - 1) * CALLS_PAGE_SIZE,
  };
}

export function callHistoryPageCount(total: number, pageSize: number = CALLS_PAGE_SIZE): number {
  return Math.max(1, Math.ceil(total / pageSize));
}

export function buildCallHistoryHref({
  query,
  status,
  range,
  page,
}: Pick<CallHistoryNavigation, "query" | "status" | "range" | "page">): string {
  const params = new URLSearchParams();
  if (query) {
    params.set("q", query);
  }
  if (status !== "all") {
    params.set("status", status);
  }
  if (range !== "all") {
    params.set("range", range);
  }
  if (page > 1) {
    params.set("page", String(page));
  }
  const queryString = params.toString();
  return queryString ? `/dashboard/calls?${queryString}` : "/dashboard/calls";
}
