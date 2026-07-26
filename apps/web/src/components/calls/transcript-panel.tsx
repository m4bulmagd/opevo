import type { CallTranscriptLine } from "@/lib/types/calls";

export function TranscriptPanel({ transcript }: { transcript: CallTranscriptLine[] }) {
  if (transcript.length === 0) {
    return <p className="text-sm text-text-secondary">No transcript is available for this call.</p>;
  }

  return (
    <ol className="flex flex-col divide-y divide-border/70">
      {transcript.map((line) => (
        <li
          className="grid gap-2 py-4 first:pt-0 last:pb-0 sm:grid-cols-[7rem_minmax(0,1fr)]"
          key={`${line.sequence_number}-${line.created_at}`}
        >
          <span className="font-semibold text-text-tertiary text-xs uppercase tracking-wide">{line.speaker}</span>
          <p className="text-sm text-text-primary leading-relaxed">{line.text}</p>
        </li>
      ))}
    </ol>
  );
}
