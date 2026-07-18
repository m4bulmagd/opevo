import Link from "next/link";

import { AudioLines, ChevronRight, Phone } from "lucide-react";

import { CallOutcome } from "@/components/calls/call-outcome";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty";
import { formatCallTime, formatDuration, formatPhoneNumber } from "@/lib/formatters";
import type { CallHistoryListItem } from "@/lib/types/calls";

export function CallsTable({ calls }: { calls: CallHistoryListItem[] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Calls</CardTitle>
        <CardDescription>Review summaries, durations, and the latest routing outcomes.</CardDescription>
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
                Your visible call history will appear here after the first handled conversation.
              </EmptyDescription>
            </EmptyHeader>
          </Empty>
        ) : (
          <div className="flex flex-col gap-3">
            {calls.map((call) => (
              <div
                key={call.id}
                className="flex flex-col gap-3 rounded-xl border px-4 py-4 md:flex-row md:items-start md:justify-between"
              >
                <div className="flex min-w-0 flex-1 flex-col gap-2">
                  <div className="flex items-center gap-2">
                    <Badge variant={call.status === "completed" ? "secondary" : "outline"}>{call.status}</Badge>
                    <span className="text-muted-foreground text-xs">{formatCallTime(call.started_at)}</span>
                  </div>
                  <span className="font-medium">{formatPhoneNumber(call.caller_number)}</span>
                  <CallOutcome
                    summaryText={call.summary_text}
                    summary_status={call.summary_status}
                    caller_intent={call.caller_intent}
                    action_items={call.action_items}
                    sentiment={call.sentiment}
                    follow_up_required={call.follow_up_required}
                  />
                  <div className="flex items-center gap-2 text-muted-foreground text-xs">
                    <AudioLines className="size-3.5" />
                    <span>{formatDuration(call.duration_seconds)}</span>
                    <span>·</span>
                    <span>{call.has_recording ? "Recording available" : "No recording"}</span>
                  </div>
                </div>
                <Button asChild variant="outline">
                  <Link href={`/dashboard/calls/${call.id}`}>
                    Open call
                    <ChevronRight data-icon="inline-end" />
                  </Link>
                </Button>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
