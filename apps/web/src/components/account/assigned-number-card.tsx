"use client";

import { type ReactNode, useState } from "react";

import Link from "next/link";

import { Copy } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardAction, CardContent, CardHeader } from "@/components/ui/card";
import { formatFrenchNumber } from "@/lib/phone-numbers";
import type { ForwardingGuide } from "@/lib/types/activation";

export function AssignedNumberCard({
  number,
  forwarding,
}: Readonly<{
  number: string | null;
  forwarding: ForwardingGuide | null;
}>): ReactNode {
  const [copyResult, setCopyResult] = useState<string | null>(null);

  const copyAssignedNumber = async () => {
    if (!number) return;

    try {
      await navigator.clipboard.writeText(number);
      setCopyResult("Assigned number copied.");
    } catch {
      setCopyResult("Copy failed. Select the number and copy it manually.");
    }
  };

  return (
    <Card aria-labelledby="assigned-number-title" role="region" size="sm">
      <CardHeader>
        <h2
          className="font-medium text-text-tertiary text-xs uppercase tracking-widest"
          data-slot="card-title"
          id="assigned-number-title"
        >
          Assigned number
        </h2>
        {number ? (
          <CardAction>
            <Button
              aria-label="Copy assigned number"
              className="min-h-11 min-w-11"
              onClick={() => void copyAssignedNumber()}
              size="icon-sm"
              type="button"
              variant="ghost"
            >
              <Copy aria-hidden="true" />
            </Button>
          </CardAction>
        ) : null}
      </CardHeader>

      <CardContent className="flex flex-col gap-3">
        <p className="font-medium text-base text-text-primary">
          {number ? formatFrenchNumber(number) : "No Presvo number is assigned yet."}
        </p>

        {forwarding ? (
          <div className="flex flex-col gap-1.5 border-border/70 border-t pt-3 text-sm text-text-secondary">
            <p>Forwarding setup is available for review.</p>
            <Button asChild className="h-auto min-h-11 justify-start px-0" variant="link">
              <Link href="/activate?milestone=forwarding">Review forwarding setup</Link>
            </Button>
          </div>
        ) : (
          <Button asChild className="h-auto min-h-11 justify-start px-0" variant="link">
            <Link href="/activate?milestone=number">Review number setup</Link>
          </Button>
        )}

        {copyResult ? (
          <p aria-live="polite" className="text-sm text-text-secondary" role="status">
            {copyResult}
          </p>
        ) : null}
      </CardContent>
    </Card>
  );
}
