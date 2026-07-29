"use client";

import { useState } from "react";

import { KeyRound, RotateCcw } from "lucide-react";

import { CapabilityBadge } from "@/components/product/capability-badge";
import { ProductSurface } from "@/components/product/product-surface";
import { Button } from "@/components/ui/button";
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select";
import { Switch } from "@/components/ui/switch";

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
  checked,
  hint,
  label,
  onCheckedChange,
}: {
  checked: boolean;
  hint: string;
  label: string;
  onCheckedChange: (checked: boolean) => void;
}) {
  return (
    <div className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-4 py-3">
      <div className="min-w-0">
        <p className="font-medium text-sm">{label}</p>
        <p className="mt-0.5 text-text-secondary text-xs leading-relaxed">{hint}</p>
      </div>
      <Switch aria-label={label} checked={checked} onCheckedChange={onCheckedChange} />
    </div>
  );
}

export function AccountSettingsPreview() {
  const [preferences, setPreferences] = useState(INITIAL_PREFERENCES);
  const [passwordPreviewOpen, setPasswordPreviewOpen] = useState(false);
  const [status, setStatus] = useState("");

  const updatePreference = <Key extends keyof PreviewPreferences>(key: Key, value: PreviewPreferences[Key]) => {
    setPreferences((current) => ({ ...current, [key]: value }));
    setStatus("A setting changed locally in Preview. No account setting was updated.");
  };

  const reset = () => {
    setPreferences(INITIAL_PREFERENCES);
    setPasswordPreviewOpen(false);
    setStatus("Preview settings reset locally.");
  };

  return (
    <section aria-label="Account settings Preview">
      <ProductSurface
        action={<CapabilityBadge status="preview" />}
        description="Explore planned account preferences using local state. Nothing here is persisted, and every change will reset on reload."
        title="Preferences, privacy & security"
      >
        <div className="grid gap-5 lg:grid-cols-2">
          <section aria-labelledby="preview-notifications-heading" className="rounded-xl border border-border p-4">
            <h3 className="font-semibold text-sm" id="preview-notifications-heading">
              Notifications
            </h3>
            <div className="mt-2 divide-y divide-border">
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
          </section>

          <div className="grid gap-5">
            <section aria-labelledby="preview-privacy-heading" className="rounded-xl border border-border p-4">
              <h3 className="font-semibold text-sm" id="preview-privacy-heading">
                Privacy & recordings
              </h3>
              <div className="mt-2">
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
              </div>
            </section>

            <section aria-labelledby="preview-security-heading" className="rounded-xl border border-border p-4">
              <h3 className="font-semibold text-sm" id="preview-security-heading">
                Security
              </h3>
              <div className="mt-2 divide-y divide-border">
                <div className="grid gap-3 py-3 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-center sm:gap-4">
                  <div className="min-w-0">
                    <p className="font-medium text-sm">Password</p>
                    <p className="mt-0.5 text-text-secondary text-xs">
                      Preview the placement of a password recovery flow.
                    </p>
                  </div>
                  <Button
                    className="min-h-11 justify-self-start sm:justify-self-end"
                    onClick={() => {
                      setPasswordPreviewOpen(true);
                      setStatus("Password flow changed locally in Preview. No account setting was updated.");
                    }}
                    size="sm"
                    variant="outline"
                  >
                    <KeyRound aria-hidden data-icon="inline-start" />
                    Preview password flow
                  </Button>
                </div>
                <PreferenceRow
                  checked={preferences.twoFactor}
                  hint="Preview an extra verification step during sign-in."
                  label="Two-factor authentication"
                  onCheckedChange={(checked) => updatePreference("twoFactor", checked)}
                />
              </div>
              {passwordPreviewOpen ? (
                <p className="mt-3 rounded-lg bg-primary-soft px-3 py-2 text-accent-foreground text-xs">
                  Password recovery layout previewed locally; no password email was sent.
                </p>
              ) : null}
            </section>
          </div>
        </div>

        <div className="mt-5 flex flex-col gap-3 border-border border-t pt-5 sm:flex-row sm:items-center sm:justify-between">
          <p aria-label="Account settings Preview status" className="min-h-5 text-text-secondary text-xs" role="status">
            {status}
          </p>
          <Button className="min-h-11 shrink-0" onClick={reset} variant="ghost">
            <RotateCcw aria-hidden data-icon="inline-start" />
            Reset settings Preview
          </Button>
        </div>
      </ProductSurface>
    </section>
  );
}
