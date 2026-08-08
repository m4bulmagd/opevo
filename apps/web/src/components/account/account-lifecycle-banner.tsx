import Link from "next/link";

import { CircleAlert, CirclePause } from "lucide-react";

import { Alert, AlertAction, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import type { AccountStatus } from "@/lib/types/account";

export function AccountLifecycleBanner({ account }: { account: AccountStatus }) {
  if (account.status === "active") {
    return null;
  }

  const needsAttention =
    account.deactivation?.state === "attention_required" || account.blocker === "deactivation_attention_required";

  if (needsAttention) {
    return (
      <Alert aria-live="polite">
        <CircleAlert />
        <AlertTitle>Account cleanup needs attention</AlertTitle>
        <AlertDescription>
          <p className="font-medium text-foreground">Opevo is no longer accepting new calls</p>
          <p>Your retained data remains available. Open Account for the latest cleanup guidance.</p>
        </AlertDescription>
        <AlertAction>
          <Button asChild className="min-h-11" size="sm" variant="outline">
            <Link href="/dashboard/account">View account</Link>
          </Button>
        </AlertAction>
      </Alert>
    );
  }

  if (account.status === "deactivating") {
    return (
      <Alert aria-live="polite">
        <CirclePause />
        <AlertTitle>Opevo is no longer accepting new calls</AlertTitle>
        <AlertDescription>
          <p className="font-medium text-foreground">Finishing account deactivation</p>
          <p>Your history remains available while subscription and number cleanup completes.</p>
        </AlertDescription>
        <AlertAction>
          <Button asChild className="min-h-11" size="sm" variant="outline">
            <Link href="/dashboard/account">View account</Link>
          </Button>
        </AlertAction>
      </Alert>
    );
  }

  return (
    <Alert aria-live="polite">
      <CircleAlert />
      <AlertTitle>Opevo is inactive</AlertTitle>
      <AlertDescription>
        Historical calls, recordings, billing, and saved configuration remain available.
      </AlertDescription>
      <AlertAction>
        <Button asChild className="min-h-11" size="sm" variant="outline">
          <Link href="/dashboard/account">View account</Link>
        </Button>
      </AlertAction>
    </Alert>
  );
}
