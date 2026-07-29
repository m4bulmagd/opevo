import { CircleAlert, CircleCheck, PhoneCall, Sparkles } from "lucide-react";

import { CallOutcome } from "@/components/calls/call-outcome";
import { ProductSurface } from "@/components/product/product-surface";
import { StatusSurface, type StatusSurfaceTone } from "@/components/product/status-surface";
import { Badge } from "@/components/ui/badge";
import { formatCallTime, formatDuration, formatMinutes, formatPhoneNumber, toTitleCase } from "@/lib/formatters";
import type { CallDetail } from "@/lib/types/calls";

function statusPresentation(status: string): {
  icon: typeof PhoneCall;
  tone: StatusSurfaceTone;
} {
  if (status === "completed") {
    return { icon: CircleCheck, tone: "ready" };
  }
  if (status === "failed") {
    return { icon: CircleAlert, tone: "warning" };
  }
  return { icon: PhoneCall, tone: "processing" };
}

export function CallStatusSurface({ call }: { call: CallDetail }) {
  const status = toTitleCase(call.status);
  const presentation = statusPresentation(call.status);
  const StatusIcon = presentation.icon;

  return (
    <StatusSurface
      description={
        call.ended_at
          ? `Started ${formatCallTime(call.started_at)} · Ended ${formatCallTime(call.ended_at)}`
          : `Started ${formatCallTime(call.started_at)}`
      }
      icon={<StatusIcon />}
      label={`Call status: ${status}`}
      title={status}
      tone={presentation.tone}
    />
  );
}

export function CallSummaryCard({ call }: { call: CallDetail }) {
  return (
    <ProductSurface
      description="Stored summary, intent, and next-step signals for this conversation."
      title={
        <span className="flex items-center gap-2">
          <Sparkles aria-hidden className="size-4 text-muted-foreground" />
          Generated summary
        </span>
      }
    >
      <CallOutcome
        summaryText={call.summary_text}
        summary_status={call.summary_status}
        caller_intent={call.caller_intent}
        action_items={call.action_items}
        sentiment={call.sentiment}
        follow_up_required={call.follow_up_required}
      />
    </ProductSurface>
  );
}

export function CallMetadataCard({ call }: { call: CallDetail }) {
  return (
    <ProductSurface description="Stored identifiers, timing, and usage for this call." title="Metadata" tone="subtle">
      <dl className="space-y-3 text-sm">
        <div className="flex items-center justify-between gap-3">
          <dt className="text-text-tertiary">Status</dt>
          <dd>
            <Badge variant={call.status === "failed" ? "destructive" : "secondary"}>{toTitleCase(call.status)}</Badge>
          </dd>
        </div>
        <div className="flex justify-between gap-3">
          <dt className="text-text-tertiary">Call ID</dt>
          <dd className="whitespace-nowrap text-right font-mono text-[10px]">{call.id}</dd>
        </div>
        <div className="flex justify-between gap-3">
          <dt className="text-text-tertiary">Caller</dt>
          <dd className="text-right font-medium">{formatPhoneNumber(call.caller_number)}</dd>
        </div>
        <div className="flex justify-between gap-3">
          <dt className="text-text-tertiary">Started</dt>
          <dd className="text-right font-medium">
            <time dateTime={call.started_at ?? undefined}>{formatCallTime(call.started_at)}</time>
          </dd>
        </div>
        <div className="flex justify-between gap-3">
          <dt className="text-text-tertiary">Ended</dt>
          <dd className="text-right font-medium">
            <time dateTime={call.ended_at ?? undefined}>{formatCallTime(call.ended_at)}</time>
          </dd>
        </div>
        <div className="flex justify-between gap-3">
          <dt className="text-text-tertiary">Duration</dt>
          <dd className="font-medium">{formatDuration(call.duration_seconds)}</dd>
        </div>
        <div className="flex justify-between gap-3">
          <dt className="text-text-tertiary">Charged</dt>
          <dd className="font-medium">
            {call.minutes_charged === null ? "Not available" : formatMinutes(call.minutes_charged)}
          </dd>
        </div>
      </dl>
    </ProductSurface>
  );
}
