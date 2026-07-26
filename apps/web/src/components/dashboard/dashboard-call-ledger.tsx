import Link from "next/link";

import { ArrowRight, Phone } from "lucide-react";

import { DataLedger } from "@/components/product/data-ledger";
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
    <DataLedger
      empty={
        <Empty>
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
      }
      header={
        <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
          <div className="space-y-1.5">
            <h2 className="font-semibold text-lg text-text-primary tracking-tight">Recent calls</h2>
            <p className="text-sm text-text-secondary">The latest conversations handled by your receptionist.</p>
          </div>
          <Button asChild className="min-h-11 self-start" variant="ghost">
            <Link href="/dashboard/calls">
              View all calls
              <ArrowRight data-icon="inline-end" />
            </Link>
          </Button>
        </div>
      }
      label="Recent calls"
    >
      {calls.map((call) => (
        <DataLedger.Row key={call.id}>
          <DataLedger.Cell label="Caller" primary>
            <Link
              aria-label={callLinkLabel(call)}
              className="inline-flex min-h-11 items-center rounded-sm font-semibold underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              href={`/dashboard/calls/${call.id}`}
            >
              {formatPhoneNumber(call.caller_number)}
            </Link>
          </DataLedger.Cell>
          <DataLedger.Cell label="Intent">
            {call.caller_intent ?? (call.summary_status === "processing" ? "Summary processing" : "Not available")}
          </DataLedger.Cell>
          <DataLedger.Cell label="Follow-up">
            <Badge variant={call.follow_up_required ? "default" : "secondary"}>
              {followUpLabel(call.follow_up_required)}
            </Badge>
          </DataLedger.Cell>
          <DataLedger.Cell label="Duration">{formatDuration(call.duration_seconds)}</DataLedger.Cell>
          <DataLedger.Cell label="Started">
            <time dateTime={call.started_at ?? undefined}>{formatCallTime(call.started_at)}</time>
          </DataLedger.Cell>
        </DataLedger.Row>
      ))}
    </DataLedger>
  );
}
