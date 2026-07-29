"use client";

import { type FormEvent, useState } from "react";

import Link from "next/link";
import { useRouter } from "next/navigation";

import { SlidersHorizontal } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select";
import { buildCallHistoryHref, callHistoryPageCount } from "@/lib/calls/call-history-navigation";
import type { CallHistoryDateRange, CallHistoryStatusFilter } from "@/lib/types/calls";

export function CallHistorySearch({
  query,
  status,
  range,
  total,
}: {
  query: string;
  status: CallHistoryStatusFilter;
  range: CallHistoryDateRange;
  total: number;
}) {
  const router = useRouter();
  const [draftQuery, setDraftQuery] = useState(query);
  const [draftStatus, setDraftStatus] = useState(status);
  const [draftRange, setDraftRange] = useState(range);
  const hasFilters = Boolean(query) || status !== "all" || range !== "all";

  function applyFilters(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    router.push(
      buildCallHistoryHref({
        query: draftQuery.trim(),
        status: draftStatus,
        range: draftRange,
        page: 1,
      }),
    );
  }

  return (
    // biome-ignore lint/a11y/useSemanticElements: A form with role=search supports the project's browser and test matrix.
    <form
      action="/dashboard/calls"
      method="get"
      onSubmit={applyFilters}
      className="surface-card grid gap-4 p-4 sm:p-5 lg:grid-cols-[minmax(0,1fr)_11rem_11rem_auto] lg:items-end"
      role="search"
    >
      <div className="flex min-w-0 flex-1 flex-col gap-2">
        <label htmlFor="call-search" className="font-medium text-sm">
          Search calls
        </label>
        <Input
          id="call-search"
          name="q"
          type="search"
          maxLength={100}
          value={draftQuery}
          onChange={(event) => setDraftQuery(event.target.value)}
          placeholder="Caller number or summary"
          className="min-h-11"
        />
      </div>
      <div className="flex min-w-0 flex-col gap-2">
        <label className="font-medium text-sm" htmlFor="call-status">
          Status
        </label>
        <NativeSelect
          aria-label="Filter by status"
          className="w-full [&_select]:min-h-11"
          value={draftStatus}
          onChange={(event) => setDraftStatus(event.target.value as CallHistoryStatusFilter)}
          id="call-status"
          name="status"
        >
          <NativeSelectOption value="all">All statuses</NativeSelectOption>
          <NativeSelectOption value="completed">Completed</NativeSelectOption>
          <NativeSelectOption value="in_progress">In progress</NativeSelectOption>
          <NativeSelectOption value="failed">Failed</NativeSelectOption>
        </NativeSelect>
      </div>
      <div className="flex min-w-0 flex-col gap-2">
        <label className="font-medium text-sm" htmlFor="call-range">
          Date
        </label>
        <NativeSelect
          aria-label="Filter by date"
          className="w-full [&_select]:min-h-11"
          value={draftRange}
          onChange={(event) => setDraftRange(event.target.value as CallHistoryDateRange)}
          id="call-range"
          name="range"
        >
          <NativeSelectOption value="all">All time</NativeSelectOption>
          <NativeSelectOption value="7d">Last 7 days</NativeSelectOption>
          <NativeSelectOption value="30d">Last 30 days</NativeSelectOption>
        </NativeSelect>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <Button className="min-h-11" type="submit">
          <SlidersHorizontal aria-hidden data-icon="inline-start" />
          Apply filters
        </Button>
        {hasFilters ? (
          <Button asChild className="min-h-11" variant="ghost">
            <Link href="/dashboard/calls">Clear filters</Link>
          </Button>
        ) : null}
      </div>
      <p className="text-muted-foreground text-sm lg:col-span-4" role="status">
        {total} matching {total === 1 ? "call" : "calls"}
      </p>
    </form>
  );
}

type CallHistoryPaginationProps = {
  query: string;
  page: number;
  pageSize: number;
  total: number;
  returnedCount: number;
  status: CallHistoryStatusFilter;
  range: CallHistoryDateRange;
};

export function CallHistoryPagination({
  query,
  status,
  range,
  page,
  pageSize,
  total,
  returnedCount,
}: CallHistoryPaginationProps) {
  if (total === 0) {
    return null;
  }

  const totalPages = callHistoryPageCount(total, pageSize);
  const firstResult = (page - 1) * pageSize + 1;
  const lastResult = firstResult + returnedCount - 1;

  return (
    <nav aria-label="Call history pages" className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
      <div className="text-muted-foreground text-sm">
        <p>
          Showing {firstResult}–{lastResult} of {total} calls
        </p>
        <p>
          Page {page} of {totalPages}
        </p>
      </div>
      <div className="flex items-center gap-2">
        {page > 1 ? (
          <Button asChild className="min-h-11" variant="outline">
            <Link href={buildCallHistoryHref({ query, status, range, page: page - 1 })}>Previous</Link>
          </Button>
        ) : (
          <Button className="min-h-11" type="button" variant="outline" disabled>
            Previous
          </Button>
        )}
        {page < totalPages ? (
          <Button asChild className="min-h-11" variant="outline">
            <Link href={buildCallHistoryHref({ query, status, range, page: page + 1 })}>Next</Link>
          </Button>
        ) : (
          <Button className="min-h-11" type="button" variant="outline" disabled>
            Next
          </Button>
        )}
      </div>
    </nav>
  );
}
