import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import type { CallTranscriptLine } from "@/lib/types/calls";

export function TranscriptPanel({ transcript }: { transcript: CallTranscriptLine[] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Transcript</CardTitle>
        <CardDescription>Ordered message history for this call.</CardDescription>
      </CardHeader>
      <CardContent>
        <div className="flex flex-col gap-3">
          {transcript.map((line) => (
            <div key={`${line.sequence_number}-${line.created_at}`} className="rounded-xl border px-4 py-3">
              <p className="text-muted-foreground text-xs">{line.speaker}</p>
              <p className="mt-2 text-sm">{line.text}</p>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}
