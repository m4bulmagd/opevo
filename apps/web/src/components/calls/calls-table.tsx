import Link from "next/link";

import { ArrowRight, Phone } from "lucide-react";

import { DataLedger } from "@/components/product/data-ledger";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty";
import { formatCallTime, formatDuration, formatPhoneNumber, toTitleCase } from "@/lib/formatters";
import type { CallHistoryListItem } from "@/lib/types/calls";

function intentLabel(call: CallHistoryListItem) {
  if (call.caller_intent) return call.caller_intent;
  if (call.summary_status === "processing") return "Summary processing";
  if (call.summary_status === "unavailable") return "Intent unavailable";
  return "Not provided";
}

function followUpLabel(value: boolean | null) {
  if (value === null) return "Follow-up unknown";
  return value ? "Follow-up needed" : "No follow-up needed";
}

function callLinkLabel(call: CallHistoryListItem) {
  return [
    `Open call from ${formatPhoneNumber(call.caller_number)}`,
    `status ${toTitleCase(call.status)}`,
    `intent ${intentLabel(call)}`,
    followUpLabel(call.follow_up_required),
    `duration ${formatDuration(call.duration_seconds)}`,
    `started ${formatCallTime(call.started_at)}`,
  ].join(", ");
}

function EmptyCallHistory({ query }: { query: string }) {
  return (
    <Empty>
      <EmptyHeader>
        <EmptyMedia variant="icon">
          <Phone />
        </EmptyMedia>
        <EmptyTitle>{query ? `No calls match “${query}”` : "No calls yet"}</EmptyTitle>
        <EmptyDescription>
          {query
            ? `No stored call matches your search for “${query}”. Try another caller number or summary phrase.`
            : "Your visible call history will appear here after the first handled conversation."}
        </EmptyDescription>
      </EmptyHeader>
      {query ? (
        <Button asChild className="min-h-11" variant="outline">
          <Link href="/dashboard/calls">Clear search</Link>
        </Button>
      ) : null}
    </Empty>
  );
}

export function CallsTable({ calls, query = "" }: { calls: CallHistoryListItem[]; query?: string }) {
  return (
    <DataLedger
      empty={<EmptyCallHistory query={query} />}
      header={
        <div className="space-y-1.5">
          <h2 className="font-semibold text-lg text-text-primary tracking-tight">Call history</h2>
          <p className="text-sm text-text-secondary">
            Caller context, next-step signals, and timing from stored conversations.
          </p>
        </div>
      }
      label="Call history"
      mode="table"
    >
      {calls.map((call) => (
        <DataLedger.Row key={call.id}>
          <DataLedger.Cell label="Caller" primary>
            <div className="flex min-w-0 flex-col items-start gap-1">
              <Link
                aria-label={callLinkLabel(call)}
                className="inline-flex min-h-11 min-w-0 items-center rounded-sm font-semibold underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                href={`/dashboard/calls/${call.id}`}
              >
                <span className="truncate">{formatPhoneNumber(call.caller_number)}</span>
                <ArrowRight aria-hidden className="ml-1.5 size-3.5 shrink-0" />
              </Link>
              {call.summary_text ? (
                <span className="line-clamp-2 max-w-md font-normal text-text-tertiary text-xs">
                  {call.summary_text}
                </span>
              ) : null}
            </div>
          </DataLedger.Cell>
          <DataLedger.Cell label="Intent">{intentLabel(call)}</DataLedger.Cell>
          <DataLedger.Cell label="Follow-up">
            <Badge variant={call.follow_up_required ? "default" : "secondary"}>
              {followUpLabel(call.follow_up_required)}
            </Badge>
          </DataLedger.Cell>
          <DataLedger.Cell hideAt="sm" label="Duration">
            {formatDuration(call.duration_seconds)}
          </DataLedger.Cell>
          <DataLedger.Cell hideAt="md" label="Started">
            <time dateTime={call.started_at ?? undefined}>{formatCallTime(call.started_at)}</time>
          </DataLedger.Cell>
        </DataLedger.Row>
      ))}
    </DataLedger>
  );
}
