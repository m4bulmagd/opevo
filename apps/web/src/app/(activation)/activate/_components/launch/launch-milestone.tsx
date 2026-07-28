"use client";

import { useRef, useState } from "react";

import { useRouter } from "next/navigation";

import { CheckCircle2 } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import type { ActivationActionResult, ActivationSnapshot } from "@/lib/types/activation";

import { goLiveAction, simulateDevelopmentForwardedCallAction } from "../../actions";
import { getActionableReadinessBlockers, ReadinessReview } from "./readiness-review";
import { VerificationCountdown } from "./verification-countdown";

type LaunchMilestoneProps = {
  snapshot: ActivationSnapshot;
  localVerification: boolean;
};

type LaunchCommand = () => Promise<ActivationActionResult>;

export function LaunchMilestone({ snapshot, localVerification }: LaunchMilestoneProps) {
  const router = useRouter();
  const pendingRef = useRef(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const verified = snapshot.completed_milestones.includes("forwarding_verified");
  const readinessBlockers = verified
    ? snapshot.runtime_readiness.blockers
    : Array.from(new Set([...snapshot.runtime_readiness.blockers, "forwarding_not_verified"]));
  const actionableBlockers = getActionableReadinessBlockers(readinessBlockers);

  const runCommand = async (command: LaunchCommand, fallbackMessage: string, onSuccess = () => router.refresh()) => {
    if (pendingRef.current) return;
    pendingRef.current = true;
    setPending(true);
    setError(null);
    let accepted = false;
    try {
      const result = await command();
      if (result.status === "error") {
        setError(result.message);
        return;
      }
      accepted = true;
      onSuccess();
    } catch {
      accepted = false;
      setError(fallbackMessage);
    } finally {
      if (!accepted) {
        pendingRef.current = false;
        setPending(false);
      }
    }
  };

  if (snapshot.stage === "verification_window_open") {
    const expiresAt = snapshot.activation.verification_window_expires_at;
    return (
      <div className="flex flex-col gap-5">
        <div className="flex flex-col gap-2">
          <div className="flex flex-wrap items-center gap-2">
            <p className="font-semibold text-lg">Verification window open</p>
            {localVerification ? <Badge variant="secondary">Local development</Badge> : null}
          </div>
          <p className="max-w-2xl text-muted-foreground text-sm leading-6">
            Call your existing business number from another phone and leave it unanswered. Presvo will detect the
            conditionally forwarded call.
          </p>
        </div>
        <div className="flex items-baseline justify-between gap-4 rounded-xl border border-border bg-muted/30 p-4">
          <span className="text-muted-foreground text-sm">Time remaining</span>
          {expiresAt ? (
            <VerificationCountdown key={expiresAt} evaluatedAt={snapshot.evaluated_at} expiresAt={expiresAt} />
          ) : (
            <span className="font-semibold tabular-nums">--:--</span>
          )}
        </div>
        {error ? (
          <Alert variant="destructive">
            <AlertTitle>The verification call did not complete</AlertTitle>
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        ) : null}
        {localVerification ? (
          <Button
            type="button"
            className="min-h-11 self-start"
            variant="outline"
            disabled={pending}
            onClick={() =>
              void runCommand(
                () => simulateDevelopmentForwardedCallAction({}),
                "We couldn't simulate the forwarded call. Refresh and try again.",
                () => {
                  pendingRef.current = false;
                  setPending(false);
                  router.push("/activate?milestone=launch");
                },
              )
            }
          >
            {pending ? <Spinner /> : null}
            Simulate forwarded call
          </Button>
        ) : null}
      </div>
    );
  }

  if (snapshot.stage === "activating") {
    return (
      <div className="flex items-start gap-3" role="status" aria-live="polite">
        <Spinner className="mt-1" aria-hidden="true" />
        <div>
          <p className="font-semibold text-lg">Bringing Presvo live</p>
          <p className="mt-1 text-muted-foreground text-sm leading-6">
            We are applying the verified routing configuration. This page refreshes while the change completes.
          </p>
        </div>
      </div>
    );
  }

  if (snapshot.stage === "runtime_paused") {
    return (
      <div className="flex flex-col gap-5">
        <Alert variant="destructive">
          <AlertTitle>Presvo is not active</AlertTitle>
          <AlertDescription>
            The latest snapshot does not prove that call routing is live. Refresh before taking any further action.
          </AlertDescription>
        </Alert>
        {snapshot.activation.last_failure_code ? (
          <p className="font-mono text-muted-foreground text-xs">Reference: {snapshot.activation.last_failure_code}</p>
        ) : null}
        {actionableBlockers.length > 0 ? <ReadinessReview blockers={readinessBlockers} /> : null}
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-5">
      {verified ? (
        <div className="flex items-start gap-3 rounded-xl border border-border bg-muted/30 p-4">
          <CheckCircle2 aria-hidden="true" className="mt-0.5 size-5 text-primary" />
          <div>
            <p className="font-semibold">Forwarding verified</p>
            <p className="mt-1 text-muted-foreground text-sm">
              The test call reached Presvo. Your receptionist is still off until you approve launch.
            </p>
          </div>
        </div>
      ) : null}

      {snapshot.activation.last_failure_code ? (
        <Alert variant="destructive">
          <AlertTitle>We couldn't bring Presvo live</AlertTitle>
          <AlertDescription>Nothing was marked active. Review the checks below, then try again.</AlertDescription>
        </Alert>
      ) : null}
      {snapshot.activation.last_failure_code ? (
        <p className="font-mono text-muted-foreground text-xs">Reference: {snapshot.activation.last_failure_code}</p>
      ) : null}

      <ReadinessReview blockers={readinessBlockers} />

      {snapshot.stage === "ready_to_activate" && actionableBlockers.length === 0 ? (
        <div className="flex flex-col items-start gap-3">
          {error ? (
            <Alert variant="destructive">
              <AlertTitle>Go-live did not start</AlertTitle>
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          ) : null}
          <Button
            className="min-h-11"
            type="button"
            size="lg"
            disabled={pending}
            onClick={() =>
              void runCommand(() => goLiveAction({}), "We couldn't bring Presvo live. Refresh and try again.")
            }
          >
            {pending ? <Spinner /> : null}
            Go live
          </Button>
          <p className="max-w-xl text-muted-foreground text-xs leading-5">
            This is the explicit launch step. Presvo will not answer forwarded calls before activation completes.
          </p>
        </div>
      ) : null}
    </div>
  );
}
