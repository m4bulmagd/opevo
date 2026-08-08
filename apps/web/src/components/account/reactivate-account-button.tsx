"use client";

import { useState, useTransition } from "react";

import { RotateCcw } from "lucide-react";
import { toast } from "sonner";

import { reactivateAccount } from "@/app/(app)/dashboard/account/actions";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";

export function ReactivateAccountButton({ reactivationAllowed }: { reactivationAllowed: boolean }) {
  const [feedback, setFeedback] = useState<string | null>(null);
  const [isPending, startTransition] = useTransition();

  const handleReactivation = () => {
    setFeedback(null);
    startTransition(async () => {
      const result = await reactivateAccount();
      if (result.status === "success") {
        toast.success(result.message);
        window.location.assign(result.url);
        return;
      }

      setFeedback(result.message);
      toast.error(result.message);
    });
  };

  return (
    <>
      <Button
        className="min-h-11"
        onClick={handleReactivation}
        disabled={isPending || !reactivationAllowed}
        aria-describedby={!reactivationAllowed ? "reactivation-unavailable" : undefined}
      >
        {isPending ? <Spinner data-icon="inline-start" /> : <RotateCcw data-icon="inline-start" />}
        Reactivate Opevo
      </Button>
      {!reactivationAllowed ? (
        <p id="reactivation-unavailable" className="text-muted-foreground text-sm">
          Reactivation will become available after cleanup finishes.
        </p>
      ) : null}
      {feedback ? (
        <p className="text-destructive text-sm" role="alert" aria-live="polite">
          {feedback}
        </p>
      ) : null}
    </>
  );
}
