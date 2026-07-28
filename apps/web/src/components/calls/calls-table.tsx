import Link from "next/link";

import { ArrowRight, Mic, MicOff, Phone } from "lucide-react";

import { Badge } from "@/components/ui/badge";
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

function EmptyCallHistory({ isFiltered }: { isFiltered: boolean }) {
  return (
    <Empty>
      <EmptyHeader>
        <EmptyMedia variant="icon">
          <Phone />
        </EmptyMedia>
        <EmptyTitle>{isFiltered ? "No calls match your filters" : "No calls yet"}</EmptyTitle>
        <EmptyDescription>
          {isFiltered
            ? "Try a different search term, status, or date range."
            : "Your visible call history will appear here after the first handled conversation."}
        </EmptyDescription>
      </EmptyHeader>
    </Empty>
  );
}

function statusVariant(status: string): "default" | "destructive" | "secondary" {
  if (status === "completed") return "default";
  if (status === "failed") return "destructive";
  return "secondary";
}

function CallHistoryCell({
  children,
  label,
  primary = false,
}: {
  children: React.ReactNode;
  label: string;
  primary?: boolean;
}) {
  return (
    <td
      className={`grid min-w-0 grid-cols-[6.25rem_minmax(0,1fr)] items-start gap-3 px-1 py-2 text-sm md:table-cell md:px-4 md:py-3 md:align-top ${
        primary ? "text-text-primary" : "text-text-secondary"
      }`}
    >
      <span className="font-medium text-text-tertiary text-xs md:hidden">{label}</span>
      <div className="min-w-0">{children}</div>
    </td>
  );
}

export function CallsTable({ calls, isFiltered = false }: { calls: CallHistoryListItem[]; isFiltered?: boolean }) {
  return (
    <section className="surface-card overflow-hidden">
      <header className="border-border/70 border-b px-4 py-4 sm:px-6">
        <div className="space-y-1.5">
          <h2 className="font-semibold text-lg text-text-primary tracking-tight">Call history</h2>
          <p className="text-sm text-text-secondary">
            Caller context, next-step signals, and timing from stored conversations.
          </p>
        </div>
      </header>
      {calls.length > 0 ? (
        <div className="p-3 md:p-0">
          <table aria-label="Call history" className="block w-full border-separate md:table md:border-spacing-0">
            <thead className="hidden border-border/70 border-b bg-surface-subtle/50 md:table-header-group">
              <tr>
                {["Caller", "Intent", "Follow-up", "Status", "Duration", "Started", "Recording"].map((label) => (
                  <th className="px-4 py-3 text-left font-medium text-text-tertiary text-xs" key={label} scope="col">
                    {label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="grid gap-3 md:table-row-group">
              {calls.map((call) => (
                <tr
                  className="grid rounded-xl border border-border/70 bg-surface p-3 shadow-card transition-colors hover:bg-surface-subtle/40 md:table-row md:rounded-none md:border-0 md:bg-transparent md:p-0 md:shadow-none"
                  data-slot="call-history-row"
                  key={call.id}
                >
                  <CallHistoryCell label="Caller" primary>
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
                  </CallHistoryCell>
                  <CallHistoryCell label="Intent">{intentLabel(call)}</CallHistoryCell>
                  <CallHistoryCell label="Follow-up">
                    <Badge variant={call.follow_up_required ? "default" : "secondary"}>
                      {followUpLabel(call.follow_up_required)}
                    </Badge>
                  </CallHistoryCell>
                  <CallHistoryCell label="Status">
                    <Badge variant={statusVariant(call.status)}>{toTitleCase(call.status)}</Badge>
                  </CallHistoryCell>
                  <CallHistoryCell label="Duration">{formatDuration(call.duration_seconds)}</CallHistoryCell>
                  <CallHistoryCell label="Started">
                    <time dateTime={call.started_at ?? undefined}>{formatCallTime(call.started_at)}</time>
                  </CallHistoryCell>
                  <CallHistoryCell label="Recording">
                    <span className="inline-flex items-center gap-1.5">
                      {call.has_recording ? (
                        <Mic aria-hidden className="size-3.5 text-accent-foreground" />
                      ) : (
                        <MicOff aria-hidden className="size-3.5 text-text-tertiary" />
                      )}
                      {call.has_recording ? "Available" : "Unavailable"}
                    </span>
                  </CallHistoryCell>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div aria-label="Call history empty" className="px-4 py-6 sm:px-6" role="status">
          <EmptyCallHistory isFiltered={isFiltered} />
        </div>
      )}
    </section>
  );
}
