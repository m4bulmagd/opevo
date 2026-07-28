"use client";

import { useRef, useState } from "react";

import Link from "next/link";
import { useRouter } from "next/navigation";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import type { ActivationSnapshot } from "@/lib/types/activation";

import { openVerificationWindowAction } from "../../actions";
import { ForwardingStepList } from "./forwarding-step-list";

function formatFrenchNumber(value: string): string {
  const compact = value.replace(/\D/g, "");
  if (compact.startsWith("33") && compact.length === 11) {
    return `+33 ${compact.slice(2, 3)} ${
      compact
        .slice(3)
        .match(/.{1,2}/g)
        ?.join(" ") ?? ""
    }`.trim();
  }
  return value;
}

export function ForwardingMilestone({ snapshot }: { snapshot: ActivationSnapshot }) {
  const router = useRouter();
  const pendingRef = useRef(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copyStatus, setCopyStatus] = useState<string | null>(null);
  const guide = snapshot.forwarding;
  const verified = snapshot.completed_milestones.includes("forwarding_verified");

  const startVerification = async () => {
    if (pendingRef.current) return;
    pendingRef.current = true;
    setPending(true);
    setError(null);
    let accepted = false;
    try {
      const result = await openVerificationWindowAction({});
      if (result.status === "error") {
        setError(result.message);
        return;
      }
      accepted = true;
      router.refresh();
    } catch {
      accepted = false;
      setError("We couldn't start the forwarding test. Refresh and try again.");
    } finally {
      if (!accepted) {
        pendingRef.current = false;
        setPending(false);
      }
    }
  };

  if (!guide) {
    return (
      <Alert variant="destructive">
        <AlertTitle>Forwarding guidance is unavailable</AlertTitle>
        <AlertDescription>Refresh this page. If the problem continues, review your carrier details.</AlertDescription>
      </Alert>
    );
  }

  return (
    <div className="flex flex-col gap-7">
      <div className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant="secondary">
            {guide.carrier === "other" ? "Carrier-specific setup needed" : guide.carrier}
          </Badge>
          <Badge variant="outline">Guide {guide.version}</Badge>
        </div>
        <div>
          <p className="text-muted-foreground text-sm">Your Presvo number</p>
          <p className="mt-1 font-semibold text-3xl tabular-nums tracking-tight">
            {formatFrenchNumber(guide.presvo_number)}
          </p>
        </div>
        <p className="max-w-2xl text-muted-foreground text-sm leading-6">{guide.warning}</p>
      </div>

      <ForwardingStepList guide={guide} onCopyResult={setCopyStatus} />
      {copyStatus ? (
        <p role="status" aria-live="polite" className="text-muted-foreground text-sm">
          {copyStatus}
        </p>
      ) : null}

      {verified ? (
        <div className="flex flex-col items-start gap-4 rounded-xl border border-border bg-muted/30 p-5">
          <div>
            <p className="font-semibold text-lg">Forwarding already verified</p>
            <p className="mt-1 max-w-2xl text-muted-foreground text-sm leading-6">
              These instructions remain available for reference. Your completed verification is unchanged.
            </p>
          </div>
          <Button className="min-h-11" variant="outline" asChild>
            <Link href="/activate?milestone=launch">Continue to final checks</Link>
          </Button>
        </div>
      ) : (
        <div className="flex flex-col items-start gap-4 rounded-xl border border-border bg-muted/30 p-5">
          <div className="flex flex-col gap-2">
            <p className="font-semibold text-lg">Test your missed-call forwarding</p>
            <p className="max-w-2xl text-muted-foreground text-sm leading-6">
              Start a ten-minute window, then call your existing business number from another phone. Do not answer the
              call; Presvo will verify that the missed call reached your new line.
            </p>
          </div>
          {error ? (
            <Alert variant="destructive">
              <AlertTitle>The test did not start</AlertTitle>
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          ) : null}
          <Button
            className="min-h-11"
            type="button"
            size="lg"
            disabled={pending}
            onClick={() => void startVerification()}
          >
            {pending ? <Spinner /> : null}
            Start 10-minute test
          </Button>
        </div>
      )}
    </div>
  );
}
