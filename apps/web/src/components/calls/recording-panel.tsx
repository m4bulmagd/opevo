import { AudioLines } from "lucide-react";

export function RecordingPanel({ recordingUrl }: { recordingUrl: string | null }) {
  if (recordingUrl) {
    return (
      // biome-ignore lint/a11y/useMediaCaption: The adjacent call transcript provides the text alternative.
      <audio aria-label="Original call recording" className="w-full" controls preload="metadata" src={recordingUrl} />
    );
  }

  return (
    <div className="flex items-center gap-3 rounded-md border border-border/70 border-dashed bg-surface-subtle/50 px-4 py-5 text-sm text-text-secondary">
      <AudioLines aria-hidden className="size-4 shrink-0" />
      <span>Recording unavailable</span>
    </div>
  );
}
