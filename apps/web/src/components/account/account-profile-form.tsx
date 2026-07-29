"use client";

import { useEffect, useRef, useState, useTransition } from "react";

import { type AccountProfileActionResult, saveAccountProfileAction } from "@/app/(app)/dashboard/account/actions";
import { UnsavedChangesBar } from "@/components/forms/unsaved-changes-bar";
import { Field, FieldDescription, FieldError, FieldGroup, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select";
import { useUnsavedChangesGuard } from "@/hooks/use-unsaved-changes-guard";
import { getAllowedAccountTimezones } from "@/lib/account-timezone";
import { normalizeFrenchNumber } from "@/lib/phone-numbers";
import type { AccountIdentity, AccountProfileValues } from "@/lib/types/account-settings";

type AccountProfileFormProps = Readonly<{
  initialProfile: AccountProfileValues;
  email: string | null;
  nameMaxLength: number;
  readOnly: boolean;
  securityMode: AccountIdentity["securityMode"];
}>;

type ProfileField = keyof AccountProfileValues;
type ProfileErrors = Partial<Record<ProfileField, string>>;

const PROFILE_FIELD_ORDER: ProfileField[] = ["owner_name", "existing_phone_e164", "business_name", "timezone"];
const SERVER_FIELD_ERROR = "Review this field and try again.";

export function AccountProfileForm({
  initialProfile,
  email,
  nameMaxLength,
  readOnly,
  securityMode,
}: AccountProfileFormProps) {
  const [baseline, setBaseline] = useState(initialProfile);
  const [draft, setDraft] = useState(initialProfile);
  const [result, setResult] = useState<AccountProfileActionResult | null>(null);
  const [errors, setErrors] = useState<ProfileErrors>({});
  const [isPending, startTransition] = useTransition();
  const fieldRefs = useRef<Partial<Record<ProfileField, HTMLInputElement | HTMLSelectElement>>>({});
  const pendingFocusRef = useRef<ProfileField | null>(null);
  const dirty = JSON.stringify(draft) !== JSON.stringify(baseline);
  const allowedTimezones = getAllowedAccountTimezones(baseline.timezone);

  useUnsavedChangesGuard(dirty && !readOnly);

  useEffect(() => {
    if (!isPending && pendingFocusRef.current) {
      fieldRefs.current[pendingFocusRef.current]?.focus();
      pendingFocusRef.current = null;
    }
  }, [isPending]);

  const updateDraft = <FieldName extends ProfileField>(field: FieldName, value: AccountProfileValues[FieldName]) => {
    setDraft((current) => ({ ...current, [field]: value }));
    setErrors((current) => ({ ...current, [field]: undefined }));
    setResult(null);
  };

  const discard = () => {
    setDraft(baseline);
    setErrors({});
    setResult(null);
  };

  const validate = (): string | null => {
    const nextErrors: ProfileErrors = {};
    if (!draft.owner_name.trim()) {
      nextErrors.owner_name = "Enter your full name.";
    } else if (draft.owner_name.length > nameMaxLength) {
      nextErrors.owner_name = `Use ${nameMaxLength} characters or fewer.`;
    }
    const normalizedPhone = normalizeFrenchNumber(draft.existing_phone_e164);
    if (!normalizedPhone) {
      nextErrors.existing_phone_e164 = "Enter a valid French number.";
    }
    if (!draft.business_name.trim()) {
      nextErrors.business_name = "Enter your business name.";
    } else if (draft.business_name.length > nameMaxLength) {
      nextErrors.business_name = `Use ${nameMaxLength} characters or fewer.`;
    }
    if (!draft.timezone.trim()) {
      nextErrors.timezone = "Choose a timezone.";
    }

    setErrors(nextErrors);
    const firstInvalid = PROFILE_FIELD_ORDER.find((field) => nextErrors[field]);
    if (firstInvalid) {
      fieldRefs.current[firstInvalid]?.focus();
      return null;
    }
    return normalizedPhone;
  };

  const save = () => {
    const normalizedPhone = validate();
    if (!normalizedPhone) {
      return;
    }

    startTransition(async () => {
      const nextResult = await saveAccountProfileAction({
        owner_name: draft.owner_name,
        business_name: draft.business_name,
        existing_phone_e164: normalizedPhone,
        timezone: draft.timezone,
      });
      setResult(nextResult);
      if (nextResult.status === "success") {
        setBaseline(nextResult.profile);
        setDraft(nextResult.profile);
        setErrors({});
      } else if (nextResult.fields?.length) {
        const nextErrors: ProfileErrors = {};
        for (const field of nextResult.fields) {
          nextErrors[field] = SERVER_FIELD_ERROR;
        }
        setErrors(nextErrors);
        pendingFocusRef.current = PROFILE_FIELD_ORDER.find((field) => nextResult.fields?.includes(field)) ?? null;
      }
    });
  };

  const feedback = result?.status === "error" ? result.message : null;

  return (
    <section aria-labelledby="account-profile-heading" className="rounded-xl border border-border bg-card p-4 sm:p-5">
      <div className="mb-5">
        <h2 className="font-semibold text-lg text-text-primary" id="account-profile-heading">
          Profile
        </h2>
        <p className="mt-1 text-sm text-text-secondary">
          Keep the details Presvo uses to serve your business accurate.
        </p>
      </div>

      <FieldGroup>
        <div className="grid gap-5 sm:grid-cols-2">
          <Field data-invalid={Boolean(errors.owner_name)}>
            <FieldLabel htmlFor="account-profile-full-name">Full name</FieldLabel>
            <Input
              aria-describedby={errors.owner_name ? "account-profile-full-name-error" : undefined}
              aria-invalid={Boolean(errors.owner_name)}
              autoComplete="name"
              className="min-h-11"
              disabled={readOnly || isPending}
              id="account-profile-full-name"
              maxLength={nameMaxLength}
              onChange={(event) => updateDraft("owner_name", event.target.value)}
              ref={(element) => {
                fieldRefs.current.owner_name = element ?? undefined;
              }}
              value={draft.owner_name}
            />
            <FieldError id="account-profile-full-name-error">{errors.owner_name}</FieldError>
          </Field>

          <Field>
            <FieldLabel htmlFor="account-profile-email">Email</FieldLabel>
            <Input
              aria-describedby={email ? undefined : "account-profile-email-description"}
              autoComplete="email"
              className="min-h-11"
              id="account-profile-email"
              readOnly
              type="email"
              value={email ?? ""}
            />
            {email ? null : (
              <FieldDescription id="account-profile-email-description">
                {securityMode === "unavailable"
                  ? "Email unavailable in local development"
                  : "Email is temporarily unavailable."}
              </FieldDescription>
            )}
          </Field>
        </div>

        <Field data-invalid={Boolean(errors.existing_phone_e164)}>
          <FieldLabel htmlFor="account-profile-phone">Personal phone</FieldLabel>
          <Input
            aria-describedby={`account-profile-phone-description${
              errors.existing_phone_e164 ? " account-profile-phone-error" : ""
            }`}
            aria-invalid={Boolean(errors.existing_phone_e164)}
            autoComplete="tel"
            className="min-h-11"
            disabled={readOnly || isPending}
            id="account-profile-phone"
            inputMode="tel"
            onChange={(event) => updateDraft("existing_phone_e164", event.target.value)}
            ref={(element) => {
              fieldRefs.current.existing_phone_e164 = element ?? undefined;
            }}
            type="tel"
            value={draft.existing_phone_e164}
          />
          <FieldDescription id="account-profile-phone-description">
            Changing this forwarding number may pause incoming calls until forwarding is verified again.
          </FieldDescription>
          <FieldError id="account-profile-phone-error">{errors.existing_phone_e164}</FieldError>
        </Field>

        <Field data-invalid={Boolean(errors.business_name)}>
          <FieldLabel htmlFor="account-profile-business-name">Business name</FieldLabel>
          <Input
            aria-describedby={errors.business_name ? "account-profile-business-name-error" : undefined}
            aria-invalid={Boolean(errors.business_name)}
            autoComplete="organization"
            className="min-h-11"
            disabled={readOnly || isPending}
            id="account-profile-business-name"
            maxLength={nameMaxLength}
            onChange={(event) => updateDraft("business_name", event.target.value)}
            ref={(element) => {
              fieldRefs.current.business_name = element ?? undefined;
            }}
            value={draft.business_name}
          />
          <FieldError id="account-profile-business-name-error">{errors.business_name}</FieldError>
        </Field>

        <Field data-invalid={Boolean(errors.timezone)}>
          <FieldLabel htmlFor="account-profile-timezone">Timezone</FieldLabel>
          <NativeSelect
            aria-describedby={errors.timezone ? "account-profile-timezone-error" : undefined}
            aria-invalid={Boolean(errors.timezone)}
            className="w-full [&_select]:min-h-11"
            disabled={readOnly || isPending}
            id="account-profile-timezone"
            onChange={(event) => updateDraft("timezone", event.target.value)}
            ref={(element) => {
              fieldRefs.current.timezone = element ?? undefined;
            }}
            value={draft.timezone}
          >
            {allowedTimezones.map((timezone) => (
              <NativeSelectOption key={timezone} value={timezone}>
                {timezone}
              </NativeSelectOption>
            ))}
          </NativeSelect>
          <FieldError id="account-profile-timezone-error">{errors.timezone}</FieldError>
        </Field>
      </FieldGroup>

      {result?.status === "success" ? (
        <p aria-live="polite" className="mt-4 text-sm text-text-secondary" role="status">
          {result.message}
        </p>
      ) : null}

      {readOnly ? null : (
        <UnsavedChangesBar dirty={dirty} feedback={feedback} onDiscard={discard} onSave={save} pending={isPending} />
      )}
    </section>
  );
}
