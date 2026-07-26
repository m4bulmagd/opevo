import Link from "next/link";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { buildCallHistoryHref, callHistoryPageCount } from "@/lib/calls/call-history-navigation";

export function CallHistorySearch({ query }: { query: string }) {
  return (
    // biome-ignore lint/a11y/useSemanticElements: A form with role=search supports the project's browser and test matrix.
    <form
      action="/dashboard/calls"
      method="get"
      className="flex flex-col gap-3 rounded-lg border border-border/70 bg-surface-subtle/50 p-4 sm:flex-row sm:items-end sm:p-5"
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
          defaultValue={query}
          placeholder="Caller number or summary"
          className="min-h-11"
        />
      </div>
      <div className="flex items-center gap-2">
        <Button className="min-h-11" type="submit">
          Search
        </Button>
        {query ? (
          <Button asChild className="min-h-11" variant="ghost">
            <Link href="/dashboard/calls">Clear</Link>
          </Button>
        ) : null}
      </div>
    </form>
  );
}

type CallHistoryPaginationProps = {
  query: string;
  page: number;
  pageSize: number;
  total: number;
  returnedCount: number;
};

export function CallHistoryPagination({ query, page, pageSize, total, returnedCount }: CallHistoryPaginationProps) {
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
            <Link href={buildCallHistoryHref(query, page - 1)}>Previous</Link>
          </Button>
        ) : (
          <Button className="min-h-11" type="button" variant="outline" disabled>
            Previous
          </Button>
        )}
        {page < totalPages ? (
          <Button asChild className="min-h-11" variant="outline">
            <Link href={buildCallHistoryHref(query, page + 1)}>Next</Link>
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
