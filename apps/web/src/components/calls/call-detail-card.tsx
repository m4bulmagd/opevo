import { CallOutcome } from "@/components/calls/call-outcome";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { formatCallTime, formatDuration, formatMinutes, formatPhoneNumber } from "@/lib/formatters";
import type { CallDetail } from "@/lib/types/calls";

export function CallDetailCard({ call }: { call: CallDetail }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{formatPhoneNumber(call.caller_number)}</CardTitle>
        <CardDescription>Call detail and summary state for this conversation.</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <div className="flex items-center gap-2">
          <Badge variant={call.status === "completed" ? "secondary" : "outline"}>{call.status}</Badge>
          <span className="text-muted-foreground text-xs">{formatCallTime(call.started_at)}</span>
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="rounded-lg border px-3 py-3">
            <p className="text-muted-foreground text-xs">Duration</p>
            <p className="mt-1 font-medium">{formatDuration(call.duration_seconds)}</p>
          </div>
          <div className="rounded-lg border px-3 py-3">
            <p className="text-muted-foreground text-xs">Charged</p>
            <p className="mt-1 font-medium">{formatMinutes(call.minutes_charged)}</p>
          </div>
        </div>
        <div className="rounded-lg border px-3 py-3">
          <CallOutcome
            summaryText={call.summary_text}
            summary_status={call.summary_status}
            caller_intent={call.caller_intent}
            action_items={call.action_items}
            sentiment={call.sentiment}
            follow_up_required={call.follow_up_required}
          />
        </div>
      </CardContent>
    </Card>
  );
}
