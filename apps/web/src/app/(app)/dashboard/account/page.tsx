import Link from "next/link";

import { AccountProfileForm } from "@/components/account/account-profile-form";
import { AccountSettingsPreview } from "@/components/account/account-settings-preview";
import { CompactAccountStatusCard } from "@/components/account/account-status-card";
import { AssignedNumberCard } from "@/components/account/assigned-number-card";
import { DeactivateAccountDialog } from "@/components/account/deactivate-account-dialog";
import { PageIntro } from "@/components/product/page-intro";
import { ProductSurface } from "@/components/product/product-surface";
import { Button } from "@/components/ui/button";
import { getAccount } from "@/lib/api/account";
import { getActivationSnapshot } from "@/lib/api/activation";
import { resolveAccountIdentity } from "@/lib/auth/account-identity";
import type { AccountProfileValues } from "@/lib/types/account-settings";

function UnavailableProfile() {
  return (
    <ProductSurface
      description="We could not load your profile details right now. Your account controls remain available below."
      title="Profile unavailable"
    >
      <Button asChild className="min-h-11" variant="outline">
        <Link href="/dashboard/account" prefetch={false}>
          Retry profile
        </Link>
      </Button>
    </ProductSurface>
  );
}

function UnavailableAssignedNumber() {
  return (
    <ProductSurface
      description="Your Presvo number is unavailable right now. Try refreshing this page shortly."
      title="Assigned number"
    >
      <p className="text-sm text-text-secondary">Assigned number unavailable.</p>
    </ProductSurface>
  );
}

export default async function AccountPage() {
  const [account, activation, identity] = await Promise.all([
    getAccount(),
    getActivationSnapshot().catch(() => null),
    resolveAccountIdentity(),
  ]);

  const initialProfile: AccountProfileValues | null = activation
    ? {
        owner_name: activation.profile.owner_name ?? "",
        business_name: activation.profile.business_name ?? "",
        existing_phone_e164: activation.profile.existing_phone_e164 ?? "",
        timezone: activation.profile.timezone ?? "Europe/Paris",
      }
    : null;

  return (
    <div className="@container/main flex flex-col gap-4 md:gap-6">
      <PageIntro
        description="Your profile, Presvo number, and account preferences."
        eyebrow="Account settings"
        title="Settings"
      />

      <div className="grid gap-5 lg:grid-cols-[minmax(0,2fr)_minmax(18rem,1fr)]">
        {activation && initialProfile ? (
          <AccountProfileForm
            email={identity.email}
            initialProfile={initialProfile}
            nameMaxLength={activation.profile_constraints.name_max_length}
            readOnly={account.status !== "active"}
            securityMode={identity.securityMode}
          />
        ) : (
          <UnavailableProfile />
        )}

        <aside className="grid content-start gap-5 pt-0 lg:pt-1">
          {activation ? (
            <AssignedNumberCard forwarding={activation.forwarding} number={activation.number.assigned_e164} />
          ) : (
            <UnavailableAssignedNumber />
          )}
          <CompactAccountStatusCard account={account} />
        </aside>
      </div>

      <AccountSettingsPreview securityMode={identity.securityMode} />

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
