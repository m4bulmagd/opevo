"use client";

import { RotateCcw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";

type UnsavedChangesBarProps = {
  dirty: boolean;
  pending: boolean;
  onDiscard: () => void;
  onSave: () => void;
  feedback?: string | null;
};

export function UnsavedChangesBar({ dirty, pending, onDiscard, onSave, feedback }: UnsavedChangesBarProps) {
  if (!dirty) {
    return null;
  }

  return (
    <div
      aria-label="Unsaved changes"
      aria-live="polite"
      className="sticky bottom-4 z-30 mt-6 flex flex-col gap-3 rounded-xl border border-border bg-card p-3 shadow-raised sm:flex-row sm:items-center sm:justify-between sm:px-4"
      role="status"
    >
      <div className="min-w-0">
        <p className="font-medium text-sm text-text-primary">You have unsaved changes</p>
        <p className="mt-0.5 min-h-4 text-text-secondary text-xs">
          {feedback ?? "Save this configuration or discard the draft before leaving."}
        </p>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        <Button className="min-h-11" disabled={pending} onClick={onDiscard} size="sm" variant="ghost">
          <RotateCcw aria-hidden data-icon="inline-start" />
          Discard
        </Button>
        <Button
          aria-label={pending ? "Saving changes" : undefined}
          className="min-h-11"
          disabled={pending}
          onClick={onSave}
          size="sm"
        >
          {pending ? <Spinner data-icon="inline-start" /> : null}
          {pending ? "Saving changes" : "Save changes"}
        </Button>
      </div>
    </div>
  );
}
