import { AccountStatusCard } from "@/components/account/account-status-card";
import { DeactivateAccountDialog } from "@/components/account/deactivate-account-dialog";
import { ClerkSetupNotice } from "@/components/auth/clerk-setup-notice";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { getAccount } from "@/lib/api/account";
import { isAppAuthConfigured } from "@/lib/auth/clerk-config";

export default async function AccountPage() {
  if (!isAppAuthConfigured) {
    return (
      <ClerkSetupNotice
        title="Account settings are unavailable"
        description="Configure Clerk in your local environment before loading account lifecycle settings."
      />
    );
  }

  const account = await getAccount();

  return (
    <div className="@container/main flex flex-col gap-6">
      <div className="flex max-w-2xl flex-col gap-2">
        <p className="font-medium text-primary text-sm">Service lifecycle</p>
        <h1 className="font-semibold text-3xl tracking-tight">Account</h1>
        <p className="text-muted-foreground">
          Review whether Presvo can serve new calls and manage the current service cycle.
        </p>
      </div>

      <AccountStatusCard account={account} />

      {account.status === "active" ? (
        <Card className="ring-destructive/20">
          <CardHeader>
            <CardTitle>
              <h2>Danger zone</h2>
            </CardTitle>
            <CardDescription>
              Deactivation immediately stops new calls, ends the current subscription, and permanently releases the
              current Presvo number.
            </CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col items-start gap-3 border-t pt-6">
            <DeactivateAccountDialog />
            <p className="max-w-2xl text-muted-foreground text-sm">
              This is not account deletion. Historical calls, recordings, billing, and saved configuration are retained.
            </p>
          </CardContent>
        </Card>
      ) : null}
    </div>
  );
}
