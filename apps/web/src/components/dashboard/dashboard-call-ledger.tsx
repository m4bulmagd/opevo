import Link from "next/link";

import { ArrowRight, ChevronRight, Mic, MicOff, Phone } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty";
import { formatCallTime, formatDuration, formatPhoneNumber } from "@/lib/formatters";
import type { CallHistoryListItem } from "@/lib/types/calls";

function followUpLabel(value: boolean | null) {
  if (value === null) return "Follow-up unknown";
  return value ? "Follow-up needed" : "No follow-up needed";
}

function callLinkLabel(call: CallHistoryListItem) {
  return [
    formatPhoneNumber(call.caller_number),
    call.caller_intent ?? (call.summary_status === "processing" ? "Summary processing" : "Not available"),
    followUpLabel(call.follow_up_required),
    formatDuration(call.duration_seconds),
    formatCallTime(call.started_at),
  ].join(", ");
}

export function DashboardCallLedger({ calls }: { calls: CallHistoryListItem[] }) {
  return (
    <section aria-labelledby="recent-calls-heading" className="surface-card p-4 sm:p-5" data-slot="recent-call-surface">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-baseline sm:justify-between">
        <div>
          <h2 className="font-semibold text-sm text-text-primary" id="recent-calls-heading">
            Recent calls
          </h2>
          <p className="mt-1 text-text-secondary text-xs">The latest conversations handled by your receptionist.</p>
        </div>
        <Button asChild className="min-h-11 self-start" variant="link">
          <Link href="/dashboard/calls">
            View all calls
            <ArrowRight data-icon="inline-end" />
          </Link>
        </Button>
      </div>
      {calls.length === 0 ? (
        <Empty className="mt-4 border-0 py-8">
          <EmptyHeader>
            <EmptyMedia variant="icon">
              <Phone />
            </EmptyMedia>
            <EmptyTitle>No calls yet</EmptyTitle>
            <EmptyDescription>
              Call history will appear here once your receptionist starts handling real conversations.
            </EmptyDescription>
          </EmptyHeader>
        </Empty>
      ) : (
        <ul aria-label="Recent calls" className="mt-4 space-y-3">
          {calls.map((call) => (
            <li data-slot="dashboard-call-card" key={call.id}>
              <Link
                aria-label={callLinkLabel(call)}
                className="group grid min-h-20 grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-3 rounded-xl border border-border bg-card p-3 outline-none transition-[background-color,border-color,box-shadow] hover:border-primary/25 hover:bg-muted/40 focus-visible:ring-3 focus-visible:ring-ring/50 sm:p-4"
                href={`/dashboard/calls/${call.id}`}
              >
                <span className="grid size-10 shrink-0 place-items-center rounded-full bg-primary-soft text-accent-foreground">
                  <Phone aria-hidden className="size-4" />
                </span>
                <span className="min-w-0">
                  <span className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
                    <span className="truncate font-semibold text-sm text-text-primary">
                      {formatPhoneNumber(call.caller_number)}
                    </span>
                    <Badge variant={call.follow_up_required ? "default" : "secondary"}>
                      {followUpLabel(call.follow_up_required)}
                    </Badge>
                  </span>
                  <span className="mt-1 block truncate text-text-secondary text-xs">
                    {call.caller_intent ??
                      (call.summary_status === "processing" ? "Summary processing" : "Not available")}
                  </span>
                  <span className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-text-tertiary text-xs">
                    <time dateTime={call.started_at ?? undefined}>{formatCallTime(call.started_at)}</time>
                    <span aria-hidden>·</span>
                    <span>{formatDuration(call.duration_seconds)}</span>
                    <span aria-hidden>·</span>
                    <span className="inline-flex items-center gap-1">
                      {call.has_recording ? (
                        <Mic aria-hidden className="size-3" />
                      ) : (
                        <MicOff aria-hidden className="size-3" />
                      )}
                      {call.has_recording ? "Recorded" : "No recording"}
                    </span>
                  </span>
                </span>
                <ChevronRight
                  aria-hidden
                  className="size-4 shrink-0 text-text-tertiary transition-colors group-hover:text-primary"
                />
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
