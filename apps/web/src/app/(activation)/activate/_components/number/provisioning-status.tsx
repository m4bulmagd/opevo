"use client";

import { useRef, useState } from "react";

import Link from "next/link";
import { useRouter } from "next/navigation";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import type { ActivationSnapshot } from "@/lib/types/activation";

import { retryProvisioningAction } from "../../actions";

type ProvisioningStatusProps = {
  snapshot: ActivationSnapshot;
};

function formatAssignedNumber(value: string): string {
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

export function ProvisioningStatus({ snapshot }: ProvisioningStatusProps) {
  const router = useRouter();
  const pendingRef = useRef(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const assignedNumber = snapshot.number.assigned_e164;

  const retry = async () => {
    if (pendingRef.current) return;
    pendingRef.current = true;
    setPending(true);
    setError(null);
    try {
      const result = await retryProvisioningAction({});
      if (result.status === "error") {
        setError(result.message);
        return;
      }
      router.refresh();
    } catch {
      setError("We couldn't retry provisioning. Refresh and try again.");
    } finally {
      pendingRef.current = false;
      setPending(false);
    }
  };

  if (assignedNumber && snapshot.number.provider_ready) {
    return (
      <div className="flex flex-col gap-5">
        <Badge className="self-start" variant="secondary">
          Number ready
        </Badge>
        <div>
          <p className="text-muted-foreground text-sm">Your French Presvo number</p>
          <p className="mt-1 font-semibold text-3xl tabular-nums tracking-tight">
            {formatAssignedNumber(assignedNumber)}
          </p>
        </div>
        <p className="max-w-2xl text-muted-foreground text-sm leading-6">
          Keep this number handy. Next, you will conditionally forward unanswered, busy, and unreachable calls to it.
        </p>
        <Button className="min-h-11 self-start" asChild size="lg">
          <Link href="/activate?milestone=forwarding">Continue to forwarding</Link>
        </Button>
      </div>
    );
  }

  if (snapshot.stage === "provisioning") {
    return (
      <div className="flex flex-col gap-4" role="status" aria-live="polite">
        <div className="flex items-center gap-3">
          <Spinner aria-hidden="true" />
          <p className="font-semibold text-lg">Provisioning your French number</p>
        </div>
        <p className="text-muted-foreground text-sm">
          This normally completes shortly. You can safely leave and return; this page refreshes while provisioning is
          active.
        </p>
      </div>
    );
  }

  const retryable = snapshot.stage === "provisioning_failed" && snapshot.number.can_retry;
  if (retryable) {
    return (
      <div className="flex flex-col gap-4">
        <Alert variant="destructive">
          <AlertTitle>Your number is not ready yet</AlertTitle>
          <AlertDescription>
            The first attempt did not complete. Retry resumes the same request; no second number will be ordered.
          </AlertDescription>
        </Alert>
        {error ? (
          <Alert variant="destructive">
            <AlertTitle>Retry did not start</AlertTitle>
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        ) : null}
        <Button className="min-h-11 self-start" disabled={pending} onClick={() => void retry()}>
          {pending ? <Spinner /> : null}
          Retry provisioning
        </Button>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <Alert variant="destructive">
        <AlertTitle>Review your business details</AlertTitle>
        <AlertDescription>
          Presvo could not provision a number from the current details. Correct the profile, then return here.
        </AlertDescription>
      </Alert>
      <p className="font-mono text-muted-foreground text-xs">Reference: number_provisioning_failed</p>
      <Button className="min-h-11 self-start" variant="outline" asChild>
        <Link href="/activate?milestone=business">Correct business profile</Link>
      </Button>
    </div>
  );
}
