import { Badge } from "@/components/ui/badge";
import type { CallSummaryFields } from "@/lib/types/calls";

type CallOutcomeProps = CallSummaryFields & {
  summaryText: string | null;
};

function summaryCopy(summaryStatus: CallSummaryFields["summary_status"], summaryText: string | null) {
  if (summaryStatus === "processing") {
    return "Summary is still processing.";
  }
  if (summaryStatus === "unavailable") {
    return "Summary unavailable.";
  }
  return summaryText ?? "Summary ready.";
}

function keyedActions(actions: string[]) {
  const occurrences = new Map<string, number>();
  return actions.map((label) => {
    const occurrence = (occurrences.get(label) ?? 0) + 1;
    occurrences.set(label, occurrence);
    return { key: `${label}:${occurrence}`, label };
  });
}

export function CallOutcome({
  summaryText,
  summary_status,
  caller_intent,
  action_items,
  follow_up_required,
}: CallOutcomeProps) {
  const visibleActions = action_items?.slice(0, 3) ?? [];
  const visibleActionEntries = keyedActions(visibleActions);
  const remainingActions = Math.max((action_items?.length ?? 0) - visibleActions.length, 0);

  return (
    <div className="flex flex-col gap-3">
      <p className="line-clamp-2 text-muted-foreground text-sm">{summaryCopy(summary_status, summaryText)}</p>
      {caller_intent ? (
        <div className="flex flex-col gap-1">
          <span className="text-muted-foreground text-xs">Outcome</span>
          <span className="font-medium text-sm">{caller_intent}</span>
        </div>
      ) : null}
      {follow_up_required === null ? null : (
        <Badge variant={follow_up_required ? "default" : "secondary"}>
          {follow_up_required ? "Follow-up needed" : "No follow-up needed"}
        </Badge>
      )}
      {visibleActions.length > 0 ? (
        <div className="flex flex-col gap-1">
          <span className="text-muted-foreground text-xs">Next actions</span>
          <ul className="flex list-disc flex-col gap-1 pl-4 text-sm">
            {visibleActionEntries.map((action) => (
              <li key={action.key}>{action.label}</li>
            ))}
          </ul>
          {remainingActions > 0 ? (
            <span className="text-muted-foreground text-xs">+{remainingActions} more</span>
          ) : null}
        </div>
      ) : null}
      {action_items?.length === 0 ? (
        <span className="text-muted-foreground text-xs">No action items suggested.</span>
      ) : null}
    </div>
  );
}
