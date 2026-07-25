export const CALLS_PAGE_SIZE = 20 as const;

export type CallHistorySearchParams = {
  q?: string | string[];
  page?: string | string[];
};

export type CallHistoryNavigation = {
  query: string;
  page: number;
  limit: typeof CALLS_PAGE_SIZE;
  offset: number;
};

function firstValue(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value;
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
    page,
    limit: CALLS_PAGE_SIZE,
    offset: (page - 1) * CALLS_PAGE_SIZE,
  };
}

export function callHistoryPageCount(total: number, pageSize = CALLS_PAGE_SIZE): number {
  return Math.max(1, Math.ceil(total / pageSize));
}

export function buildCallHistoryHref(query: string, page: number): string {
  const params = new URLSearchParams();
  if (query) {
    params.set("q", query);
  }
  if (page > 1) {
    params.set("page", String(page));
  }
  const queryString = params.toString();
  return queryString ? `/dashboard/calls?${queryString}` : "/dashboard/calls";
}
