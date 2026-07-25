import Link from "next/link";

import { CircleAlert, CirclePause } from "lucide-react";

import { Alert, AlertAction, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import type { AccountStatus } from "@/lib/types/account";

export function AccountLifecycleBanner({ account }: { account: AccountStatus }) {
  if (account.status === "active") {
    return null;
  }

  if (account.status === "deactivating") {
    return (
      <Alert aria-live="polite">
        <CirclePause />
        <AlertTitle>Presvo is no longer accepting new calls</AlertTitle>
        <AlertDescription>
          <p className="font-medium text-foreground">Finishing account deactivation</p>
          <p>Your history remains available while subscription and number cleanup completes.</p>
        </AlertDescription>
        <AlertAction>
          <Button asChild size="sm" variant="outline">
            <Link href="/dashboard/account">View account</Link>
          </Button>
        </AlertAction>
      </Alert>
    );
  }

  return (
    <Alert aria-live="polite">
      <CircleAlert />
      <AlertTitle>Presvo is inactive</AlertTitle>
      <AlertDescription>
        Historical calls, recordings, billing, and saved configuration remain available.
      </AlertDescription>
      <AlertAction>
        <Button asChild size="sm" variant="outline">
          <Link href="/dashboard/account">View account</Link>
        </Button>
      </AlertAction>
    </Alert>
  );
}
