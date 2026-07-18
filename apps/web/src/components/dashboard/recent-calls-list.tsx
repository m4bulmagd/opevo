import Link from "next/link";

import { AudioLines, Phone } from "lucide-react";

import { CallOutcome } from "@/components/calls/call-outcome";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Empty, EmptyContent, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty";
import { formatCallTime, formatDuration, formatPhoneNumber, formatRelativeTime } from "@/lib/formatters";
import type { CallHistoryListItem } from "@/lib/types/calls";

export function RecentCallsList({ calls }: { calls: CallHistoryListItem[] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Recent call activity</CardTitle>
        <CardDescription>Review the latest summaries and jump into the full call history when needed.</CardDescription>
      </CardHeader>
      <CardContent>
        {calls.length === 0 ? (
          <Empty className="border">
            <EmptyHeader>
              <EmptyMedia variant="icon">
                <Phone />
              </EmptyMedia>
              <EmptyTitle>No calls yet</EmptyTitle>
              <EmptyDescription>
                Call history will appear here once your agent starts handling real conversations.
              </EmptyDescription>
            </EmptyHeader>
            <EmptyContent />
          </Empty>
        ) : (
          <div className="flex flex-col gap-3">
            {calls.map((call) => (
              <Link
                key={call.id}
                href={`/dashboard/calls/${call.id}`}
                className="flex flex-col gap-3 rounded-xl border px-4 py-4 transition-colors hover:bg-muted/40"
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="flex flex-col gap-1">
                    <span className="font-medium">{formatPhoneNumber(call.caller_number)}</span>
                    <span className="text-muted-foreground text-xs">
                      {formatCallTime(call.started_at)} · {formatRelativeTime(call.started_at)}
                    </span>
                  </div>
                  <Badge variant={call.status === "completed" ? "secondary" : "outline"}>{call.status}</Badge>
                </div>
                <div className="flex items-center gap-2 text-muted-foreground text-xs">
                  <AudioLines className="size-3.5" />
                  <span>{formatDuration(call.duration_seconds)}</span>
                  <span>·</span>
                  <span>{call.has_recording ? "Recording available" : "No recording"}</span>
                </div>
                <CallOutcome
                  summaryText={call.summary_text}
                  summary_status={call.summary_status}
                  caller_intent={call.caller_intent}
                  action_items={call.action_items}
                  sentiment={call.sentiment}
                  follow_up_required={call.follow_up_required}
                />
              </Link>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
