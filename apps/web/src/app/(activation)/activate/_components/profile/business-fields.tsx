"use client";

import { Controller, type UseFormReturn } from "react-hook-form";

import { Field, FieldDescription, FieldError, FieldGroup, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { NativeSelect, NativeSelectOption, NativeSelectOptGroup as SelectGroup } from "@/components/ui/native-select";
import { normalizeFrenchNumber } from "@/lib/phone-numbers";
import type { BusinessProfileConstraints, BusinessProfileDraft, CarrierCode } from "@/lib/types/activation";

import { BusinessHoursEditor, createDefaultBusinessHours, validateBusinessHours } from "./business-hours-editor";
import { CarrierConfirmation } from "./carrier-confirmation";

type BusinessFieldsProps = {
  form: UseFormReturn<BusinessProfileDraft>;
  constraints: BusinessProfileConstraints;
  detectedCarrier: string | null;
  carrierLookupStatus: string | null;
  onSaveBeforeLookup: () => Promise<boolean>;
};

function detectedCode(value: string | null): CarrierCode | null {
  const normalized = value?.trim().toLocaleLowerCase() ?? "";
  if (normalized.includes("orange")) return "orange";
  if (normalized.includes("sfr")) return "sfr";
  if (normalized.includes("bouygues")) return "bouygues";
  if (normalized.includes("free")) return "free";
  if (normalized === "other") return "other";
  return null;
}

const count = (value: string | null | undefined) => value?.length ?? 0;

export function BusinessFields({
  form,
  constraints,
  detectedCarrier,
  carrierLookupStatus,
  onSaveBeforeLookup,
}: BusinessFieldsProps) {
  const draft = form.watch();
  const errors = form.formState.errors;

  return (
    <FieldGroup>
      <div className="grid gap-6 sm:grid-cols-2">
        <Field data-invalid={Boolean(errors.owner_name)}>
          <FieldLabel htmlFor="owner-name">Owner name</FieldLabel>
          <Input
            id="owner-name"
            autoComplete="name"
            maxLength={constraints.name_max_length}
            aria-invalid={Boolean(errors.owner_name)}
            {...form.register("owner_name", { required: "Add the name Opevo should use for you." })}
          />
          <FieldDescription>
            {count(draft.owner_name)} / {constraints.name_max_length}
          </FieldDescription>
          <FieldError errors={[errors.owner_name]} />
        </Field>
        <Field data-invalid={Boolean(errors.business_name)}>
          <FieldLabel htmlFor="business-name">Business name</FieldLabel>
          <Input
            id="business-name"
            autoComplete="organization"
            maxLength={constraints.name_max_length}
            aria-invalid={Boolean(errors.business_name)}
            {...form.register("business_name", { required: "Add the business name callers know." })}
          />
          <FieldDescription>
            {count(draft.business_name)} / {constraints.name_max_length}
          </FieldDescription>
          <FieldError errors={[errors.business_name]} />
        </Field>
      </div>

      <Field data-invalid={Boolean(errors.business_type)}>
        <FieldLabel htmlFor="business-type">Business type</FieldLabel>
        <Input
          autoComplete="off"
          id="business-type"
          placeholder="For example, dental practice"
          maxLength={constraints.business_type_max_length}
          aria-invalid={Boolean(errors.business_type)}
          {...form.register("business_type", { required: "Add a short business type." })}
        />
        <FieldDescription>
          {count(draft.business_type)} / {constraints.business_type_max_length}
        </FieldDescription>
        <FieldError errors={[errors.business_type]} />
      </Field>

      <Controller
        control={form.control}
        name="timezone"
        rules={{ required: "Confirm the timezone used for these opening hours." }}
        render={({ field, fieldState }) => (
          <Field data-invalid={Boolean(fieldState.error)}>
            <FieldLabel htmlFor="business-timezone">Timezone</FieldLabel>
            <NativeSelect
              id="business-timezone"
              className="w-full"
              aria-invalid={Boolean(fieldState.error)}
              value={field.value ?? "Europe/Paris"}
              onChange={field.onChange}
              onBlur={field.onBlur}
              name={field.name}
              ref={field.ref}
            >
              <SelectGroup label="France launch">
                <NativeSelectOption value="Europe/Paris">Europe/Paris</NativeSelectOption>
              </SelectGroup>
            </NativeSelect>
            <FieldDescription>Opening hours are interpreted in this local timezone.</FieldDescription>
            <FieldError errors={[fieldState.error]} />
          </Field>
        )}
      />

      <Controller
        control={form.control}
        name="business_hours"
        rules={{
          validate: (value) => (value && !validateBusinessHours(value)) || "Review the highlighted opening hours.",
        }}
        render={({ field, fieldState }) => (
          <div>
            <BusinessHoursEditor
              value={field.value ?? createDefaultBusinessHours()}
              onChange={(value) => field.onChange(value)}
              maxIntervalsPerDay={constraints.max_intervals_per_day}
              invalid={Boolean(fieldState.error)}
            />
            <FieldError errors={[fieldState.error]} />
          </div>
        )}
      />

      <Controller
        control={form.control}
        name="existing_phone_e164"
        rules={{
          validate: (value) => Boolean(value && normalizeFrenchNumber(value)) || "Enter a valid French number.",
        }}
        render={({ field, fieldState }) => (
          <CarrierConfirmation
            phoneNumber={field.value ?? ""}
            inputRef={field.ref}
            confirmedCarrier={form.getValues("confirmed_carrier") ?? null}
            phoneValidationError={fieldState.error?.message}
            carrierValidationError={errors.confirmed_carrier?.message}
            onPhoneChange={(value) => {
              field.onChange(value);
              form.setValue("confirmed_carrier", null, { shouldDirty: true });
            }}
            onConfirm={(carrier) => {
              form.setValue("confirmed_carrier", carrier, { shouldDirty: true, shouldValidate: true });
              form.clearErrors("confirmed_carrier");
            }}
            onSaveBeforeLookup={onSaveBeforeLookup}
            initialDetectedCarrier={detectedCarrier}
            initialDetectedCarrierCode={detectedCode(detectedCarrier)}
            initialLookupError={
              carrierLookupStatus === "failed" ? "We couldn't check your carrier. Choose it manually or retry." : null
            }
          />
        )}
      />
    </FieldGroup>
  );
}
