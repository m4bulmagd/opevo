import { CircleCheck, CirclePause } from "lucide-react";

import { ReactivateAccountButton } from "@/components/account/reactivate-account-button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import type { AccountStatus } from "@/lib/types/account";

const DEACTIVATION_PROGRESS_COPY: Record<NonNullable<AccountStatus["deactivation"]>["state"], string> = {
  requested: "Request accepted",
  disabling_routing: "Stopping new calls",
  canceling_subscription: "Canceling subscription",
  draining_call: "Waiting for an active call to finish",
  releasing_number: "Releasing your Presvo number",
  finalizing: "Finalizing your account",
  attention_required: "Cleanup needs additional time",
};

export function AccountStatusCard({ account }: { account: AccountStatus }) {
  if (account.status === "active") {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="flex flex-col items-start justify-between gap-3 sm:flex-row sm:items-center">
            <h2 className="flex items-center gap-2">
              <CircleCheck className="size-4 text-primary" />
              Presvo is active
            </h2>
            <Badge>{account.serving ? "Active" : "Setup required"}</Badge>
          </CardTitle>
          <CardDescription>
            {account.serving
              ? "Your account can accept new calls."
              : "Your account is active. Complete any remaining setup before accepting calls."}
          </CardDescription>
        </CardHeader>
      </Card>
    );
  }

  if (account.status === "deactivating") {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="flex flex-col items-start justify-between gap-3 sm:flex-row sm:items-center">
            <h2 className="flex items-center gap-2">
              <CirclePause className="size-4 text-muted-foreground" />
              Finishing account deactivation
            </h2>
            <Badge variant="secondary">Deactivating</Badge>
          </CardTitle>
          <CardDescription>
            <p className="font-medium text-foreground">Presvo is no longer accepting new calls</p>
            <p>Your retained data remains available while cleanup finishes.</p>
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="flex flex-col gap-1 rounded-lg border bg-muted/20 px-4 py-3">
            <span className="text-muted-foreground text-xs uppercase tracking-[0.12em]">Current progress</span>
            <span className="font-medium">
              {account.deactivation ? DEACTIVATION_PROGRESS_COPY[account.deactivation.state] : "Cleanup in progress"}
            </span>
          </div>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex flex-col items-start justify-between gap-3 sm:flex-row sm:items-center">
          <h2 className="flex items-center gap-2">
            <CirclePause className="size-4 text-muted-foreground" />
            Presvo is inactive
          </h2>
          <Badge variant="secondary">Inactive</Badge>
        </CardTitle>
        <CardDescription>
          Your calls, recordings, billing history, and saved configuration remain available. Reactivation starts a new
          subscription and provisions a new Presvo number.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col items-start gap-3">
        <ReactivateAccountButton reactivationAllowed={account.reactivation_allowed} />
      </CardContent>
    </Card>
  );
}
