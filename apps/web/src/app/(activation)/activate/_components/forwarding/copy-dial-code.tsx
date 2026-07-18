"use client";

import { Copy } from "lucide-react";

import { Button } from "@/components/ui/button";

type CopyDialCodeProps = {
  code: string;
  label: string;
  onResult: (message: string) => void;
};

export function CopyDialCode({ code, label, onResult }: CopyDialCodeProps) {
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(code);
      onResult(`Copied dial code for ${label}.`);
    } catch {
      onResult(`Copy failed for ${label}. Select the code and copy it manually.`);
    }
  };

  return (
    <Button
      type="button"
      size="sm"
      variant="outline"
      aria-label={`Copy dial code for ${label}`}
      onClick={() => void copy()}
    >
      <Copy aria-hidden="true" />
      Copy
    </Button>
  );
}
