"use client";

import { CircleAlert } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";

type ActivationErrorProps = {
  error: Error & { digest?: string };
  reset: () => void;
};

export default function ActivationError({ error: _error, reset }: ActivationErrorProps) {
  return (
    <main
      className="mx-auto flex min-h-[calc(100svh-4rem)] w-full max-w-3xl flex-1 items-center px-4 py-12 sm:px-6"
      id="activation-content"
    >
      <div className="w-full rounded-2xl border border-border bg-card p-5 shadow-card sm:p-7">
        <Alert className="border-0 bg-transparent p-0 shadow-none">
          <CircleAlert />
          <AlertTitle>We couldn&apos;t load your activation</AlertTitle>
          <AlertDescription className="flex flex-col items-start gap-4">
            <p>Your progress is safely stored. Try loading the latest activation state again.</p>
            <Button className="min-h-11" type="button" onClick={reset}>
              Try again
            </Button>
          </AlertDescription>
        </Alert>
      </div>
    </main>
  );
}
