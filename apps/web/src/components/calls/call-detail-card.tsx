import { CircleAlert, CircleCheck, PhoneCall } from "lucide-react";

import { CallOutcome } from "@/components/calls/call-outcome";
import { ProductSurface } from "@/components/product/product-surface";
import { StatusSurface, type StatusSurfaceTone } from "@/components/product/status-surface";
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

export function CallDetailCard({ call }: { call: CallDetail }) {
  const status = toTitleCase(call.status);
  const presentation = statusPresentation(call.status);
  const StatusIcon = presentation.icon;

  return (
    <div className="flex flex-col gap-6">
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
      <div className="grid gap-6 lg:grid-cols-[minmax(0,1.25fr)_minmax(18rem,0.75fr)]">
        <ProductSurface
          description="Stored summary, intent, and next-step signals for this conversation."
          title="Summary"
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
        <ProductSurface description="Timing and usage recorded for this call." title="Metadata" tone="subtle">
          <dl className="grid gap-x-6 gap-y-4 sm:grid-cols-2 lg:grid-cols-1">
            <div className="space-y-1">
              <dt className="font-medium text-text-tertiary text-xs">Caller</dt>
              <dd className="text-sm text-text-primary">{formatPhoneNumber(call.caller_number)}</dd>
            </div>
            <div className="space-y-1">
              <dt className="font-medium text-text-tertiary text-xs">Started</dt>
              <dd className="text-sm text-text-primary">
                <time dateTime={call.started_at ?? undefined}>{formatCallTime(call.started_at)}</time>
              </dd>
            </div>
            <div className="space-y-1">
              <dt className="font-medium text-text-tertiary text-xs">Ended</dt>
              <dd className="text-sm text-text-primary">
                <time dateTime={call.ended_at ?? undefined}>{formatCallTime(call.ended_at)}</time>
              </dd>
            </div>
            <div className="space-y-1">
              <dt className="font-medium text-text-tertiary text-xs">Duration</dt>
              <dd className="text-sm text-text-primary">{formatDuration(call.duration_seconds)}</dd>
            </div>
            <div className="space-y-1">
              <dt className="font-medium text-text-tertiary text-xs">Charged</dt>
              <dd className="text-sm text-text-primary">
                {call.minutes_charged === null ? "Not available" : formatMinutes(call.minutes_charged)}
              </dd>
            </div>
          </dl>
        </ProductSurface>
      </div>
    </div>
  );
}
