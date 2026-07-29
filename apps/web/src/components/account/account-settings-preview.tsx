"use client";

import { type ReactNode, useState } from "react";

import { RotateCcw } from "lucide-react";

import { ClerkSecurityButton } from "@/components/account/clerk-security-button";
import { CapabilityBadge } from "@/components/product/capability-badge";
import { ProductSurface } from "@/components/product/product-surface";
import { Button } from "@/components/ui/button";
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select";
import { Switch } from "@/components/ui/switch";

type AccountSettingsPreviewProps = Readonly<{
  securityMode: "clerk" | "unavailable";
}>;

type PreviewPreferences = {
  callSummaries: boolean;
  missedCalls: boolean;
  productUpdates: boolean;
  recording: boolean;
  retention: string;
  twoFactor: boolean;
  usageAlerts: boolean;
};

const INITIAL_PREFERENCES: PreviewPreferences = {
  callSummaries: true,
  missedCalls: true,
  productUpdates: false,
  recording: true,
  retention: "30",
  twoFactor: false,
  usageAlerts: true,
};

const NOTIFICATIONS: ReadonlyArray<{
  hint: string;
  id: keyof Pick<PreviewPreferences, "callSummaries" | "missedCalls" | "productUpdates" | "usageAlerts">;
  label: string;
}> = [
  { id: "callSummaries", label: "Call summaries", hint: "Preview an email after every answered call." },
  { id: "missedCalls", label: "Missed calls", hint: "Preview an alert when a caller hangs up early." },
  { id: "usageAlerts", label: "Usage alerts", hint: "Preview an alert near the monthly allowance." },
  { id: "productUpdates", label: "Product updates", hint: "Preview occasional Presvo product news." },
];

function PreferenceRow({
  badge,
  checked,
  hint,
  label,
  onCheckedChange,
}: {
  badge?: ReactNode;
  checked: boolean;
  hint: string;
  label: string;
  onCheckedChange: (checked: boolean) => void;
}) {
  return (
    <div className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-4 py-3">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <p className="font-medium text-sm">{label}</p>
          {badge}
        </div>
        <p className="mt-0.5 text-text-secondary text-xs leading-relaxed">{hint}</p>
      </div>
      <Switch aria-label={label} checked={checked} onCheckedChange={onCheckedChange} />
    </div>
  );
}

const PREVIEW_DESCRIPTION = "Preview only. Changes stay local and reset on reload.";

export function AccountSettingsPreview({ securityMode }: AccountSettingsPreviewProps) {
  const [preferences, setPreferences] = useState(INITIAL_PREFERENCES);
  const [status, setStatus] = useState("");

  const updatePreference = <Key extends keyof PreviewPreferences>(key: Key, value: PreviewPreferences[Key]) => {
    setPreferences((current) => ({ ...current, [key]: value }));
    setStatus("A setting changed locally in Preview. No account setting was updated.");
  };

  const reset = () => {
    setPreferences(INITIAL_PREFERENCES);
    setStatus("Preview settings reset locally.");
  };

  return (
    <div className="grid gap-5">
      <ProductSurface
        action={<CapabilityBadge status="preview" />}
        description={PREVIEW_DESCRIPTION}
        footer={
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <p aria-label="Account settings Preview status" className="min-h-5 text-xs" role="status">
              {status}
            </p>
            <Button className="min-h-11 shrink-0" onClick={reset} type="button" variant="ghost">
              <RotateCcw aria-hidden data-icon="inline-start" />
              Reset settings Preview
            </Button>
          </div>
        }
        title={
          <span className="flex flex-wrap items-center gap-2">
            Notifications <span className="sr-only">Preview</span>
          </span>
        }
      >
        <div className="divide-y divide-border">
          {NOTIFICATIONS.map((option) => (
            <PreferenceRow
              checked={preferences[option.id]}
              hint={option.hint}
              key={option.id}
              label={option.label}
              onCheckedChange={(checked) => updatePreference(option.id, checked)}
            />
          ))}
        </div>
      </ProductSurface>

      <ProductSurface
        action={<CapabilityBadge status="preview" />}
        description={PREVIEW_DESCRIPTION}
        title={
          <span className="flex flex-wrap items-center gap-2">
            Privacy &amp; recordings <span className="sr-only">Preview</span>
          </span>
        }
      >
        <PreferenceRow
          checked={preferences.recording}
          hint="Preview a disclosure before a recording would begin."
          label="Record calls"
          onCheckedChange={(checked) => updatePreference("recording", checked)}
        />
        <label className="mt-3 grid gap-2 font-medium text-sm" htmlFor="preview-retention">
          Recording retention
          <NativeSelect
            aria-label="Preview recording retention"
            className="w-full [&_select]:min-h-11"
            id="preview-retention"
            name="preview-retention"
            onChange={(event) => updatePreference("retention", event.target.value)}
            value={preferences.retention}
          >
            <NativeSelectOption value="30">30 days</NativeSelectOption>
            <NativeSelectOption value="90">90 days</NativeSelectOption>
            <NativeSelectOption value="365">12 months</NativeSelectOption>
          </NativeSelect>
        </label>
      </ProductSurface>

      <ProductSurface description={PREVIEW_DESCRIPTION} title="Security">
        <div className="divide-y divide-border">
          <div className="flex flex-col gap-3 py-3 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <p className="font-medium text-sm">Password and sign-in</p>
              <p className="mt-0.5 text-text-secondary text-xs leading-relaxed">
                {securityMode === "clerk"
                  ? "Manage your hosted account credentials through Clerk."
                  : "Password and sign-in methods are managed through Clerk in hosted accounts."}
              </p>
            </div>
            {securityMode === "clerk" ? <ClerkSecurityButton /> : null}
          </div>
          <PreferenceRow
            badge={<CapabilityBadge status="preview" />}
            checked={preferences.twoFactor}
            hint="Preview an extra verification step during sign-in."
            label="Two-factor authentication"
            onCheckedChange={(checked) => updatePreference("twoFactor", checked)}
          />
        </div>
      </ProductSurface>
    </div>
  );
}
