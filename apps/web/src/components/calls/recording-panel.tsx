import Link from "next/link";

import { AudioLines } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

export function RecordingPanel({ recordingUrl }: { recordingUrl: string | null }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Recording</CardTitle>
        <CardDescription>
          Fresh signed recording access is shown here when the object is still available.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {recordingUrl ? (
          <Button asChild>
            <Link href={recordingUrl} target="_blank" rel="noreferrer">
              Open recording
            </Link>
          </Button>
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
