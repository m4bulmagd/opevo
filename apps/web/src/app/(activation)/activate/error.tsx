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
    <main id="activation-content" className="mx-auto flex w-full max-w-3xl flex-1 items-center px-5 py-12 sm:px-8">
      <Alert>
        <CircleAlert />
        <AlertTitle>We couldn&apos;t load your activation</AlertTitle>
        <AlertDescription className="flex flex-col items-start gap-4">
          <p>Your progress is safely stored. Try loading the latest activation state again.</p>
          <Button type="button" onClick={reset}>
            Try again
          </Button>
        </AlertDescription>
      </Alert>
    </main>
  );
}
