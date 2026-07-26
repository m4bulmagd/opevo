import Link from "next/link";

import { AccountStatusCard, getAccountLifecyclePresentation } from "@/components/account/account-status-card";
import { DeactivateAccountDialog } from "@/components/account/deactivate-account-dialog";
import { ClerkSetupNotice } from "@/components/auth/clerk-setup-notice";
import { PageIntro } from "@/components/product/page-intro";
import { ProductSurface } from "@/components/product/product-surface";
import { SettingsSection } from "@/components/product/settings-section";
import { Button } from "@/components/ui/button";
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
  const lifecycle = getAccountLifecyclePresentation(account);

  return (
    <div className="@container/main flex flex-col gap-4 md:gap-6">
      <PageIntro
        description="Review your service lifecycle, account destinations, and the controls for the current Presvo service cycle."
        eyebrow="Service lifecycle"
        title="Account"
      />

      <AccountStatusCard account={account} />

      <ProductSurface
        description="Manage the account areas Presvo supports today. Session controls remain in the workspace header."
        title="Account settings"
        tone="subtle"
      >
        <SettingsSection
          action={
            <Button asChild className="min-h-11" variant="outline">
              <Link href="/dashboard/agent">Manage receptionist</Link>
            </Button>
          }
          description="Update the name, greeting, call handling, and business context your receptionist uses."
          title="Receptionist profile"
        >
          <p className="text-sm text-text-secondary">Keep customer-facing receptionist details accurate and current.</p>
        </SettingsSection>

        <SettingsSection
          action={
            <Button asChild className="min-h-11" variant="outline">
              <Link href="/dashboard/billing">View billing</Link>
            </Button>
          }
          description="Review your subscription, minute usage, and billing history."
          title="Billing and subscription"
        >
          <p className="text-sm text-text-secondary">
            Billing controls remain separate from immediate account deactivation.
          </p>
        </SettingsSection>

        <SettingsSection
          description="Authentication and session controls follow the active sign-in mode. For hosted accounts, use the workspace header account control to sign out."
          title="Session and security"
        >
          <p className="text-sm text-text-secondary">
            Your current authentication mode provides the available session controls.
          </p>
        </SettingsSection>

        <SettingsSection
          description="This bounded lifecycle state reflects whether Presvo can accept new calls."
          title="Account state"
        >
          <div className="flex items-center justify-between gap-4 rounded-md border border-border/70 bg-surface px-4 py-3">
            <span className="text-sm text-text-secondary">Current state</span>
            <span className="font-medium text-sm text-text-primary">{lifecycle.label}</span>
          </div>
        </SettingsSection>
      </ProductSurface>

      {account.status === "active" ? (
        <ProductSurface
          description="Deactivation immediately stops new calls, ends the current subscription, and permanently releases the current Presvo number."
          title="Danger zone"
          tone="danger"
        >
          <div className="flex flex-col items-start gap-3">
            <DeactivateAccountDialog />
            <p className="max-w-2xl text-sm text-text-secondary">
              Historical calls, recordings, billing history, and saved configuration are retained after deactivation.
            </p>
          </div>
        </ProductSurface>
      ) : null}
    </div>
  );
}
