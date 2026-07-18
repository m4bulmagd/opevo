import { AudioLines } from "lucide-react";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

export function RecordingPanel({ recordingUrl }: { recordingUrl: string | null }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Recording</CardTitle>
        <CardDescription>Play the original call audio through a fresh private link.</CardDescription>
      </CardHeader>
      <CardContent>
        {recordingUrl ? (
          // biome-ignore lint/a11y/useMediaCaption: The adjacent call transcript provides the text alternative.
          <audio
            aria-label="Original call recording"
            className="w-full"
            controls
            preload="metadata"
            src={recordingUrl}
          />
        ) : (
          <div className="flex items-center gap-2 rounded-lg border border-dashed px-4 py-4 text-muted-foreground text-sm">
            <AudioLines className="size-4" />
            <span>Recording unavailable</span>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
