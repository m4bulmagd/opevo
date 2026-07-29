import { MicOff } from "lucide-react";

export function RecordingPanel({ recordingUrl }: { recordingUrl: string | null }) {
  if (recordingUrl) {
    return (
      // biome-ignore lint/a11y/useMediaCaption: The adjacent call transcript provides the text alternative.
      <audio
        aria-label="Original call recording"
        className="w-full rounded-xl border border-border bg-muted/40 p-3"
        controls
        preload="metadata"
        src={recordingUrl}
      />
    );
  }

  return (
    <div className="flex items-start gap-3 rounded-xl border border-border border-dashed bg-surface-subtle/50 p-4">
      <MicOff aria-hidden className="mt-0.5 size-4 shrink-0 text-text-tertiary" />
      <div>
        <p className="font-medium text-sm text-text-primary">Recording unavailable</p>
        <p className="mt-1 text-text-secondary text-xs">
          Recording was disabled or the call ended before audio was captured.
        </p>
      </div>
    </div>
  );
}
