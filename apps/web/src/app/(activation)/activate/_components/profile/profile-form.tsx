"use client";

import { useMemo, useState } from "react";

import { useRouter } from "next/navigation";

import { useForm } from "react-hook-form";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import type { ActivationSnapshot, BusinessProfileDraft } from "@/lib/types/activation";

import { confirmProfileAction } from "../../actions";
import { BusinessFields } from "./business-fields";
import { createDefaultBusinessHours, validateBusinessHours } from "./business-hours-editor";
import { normalizeFrenchNumber } from "./carrier-confirmation";
import { ReceptionistFields } from "./receptionist-fields";
import { useProfileAutosave } from "./use-profile-autosave";

type ProfileFormProps = {
  snapshot: ActivationSnapshot;
  milestone: "business" | "receptionist";
};

function initialDraft(snapshot: ActivationSnapshot): BusinessProfileDraft {
  const profile = snapshot.profile;
  return {
    owner_name: profile.owner_name ?? "",
    business_name: profile.business_name ?? "",
    business_type: profile.business_type ?? "",
    public_description: profile.public_description ?? "",
    timezone: profile.timezone ?? "Europe/Paris",
    business_hours: profile.business_hours ?? createDefaultBusinessHours(),
    existing_phone_e164: profile.existing_phone_e164 ?? "",
    confirmed_carrier: profile.confirmed_carrier,
    receptionist_name: profile.receptionist_name ?? "",
    faqs: profile.faqs.map((faq) => ({ ...faq })),
    special_instructions: profile.special_instructions ?? "",
    escalation_notes: profile.escalation_notes ?? "",
  };
}

const nullable = (value: string | null | undefined) => value?.trim() || null;

function actionDraft(draft: BusinessProfileDraft): BusinessProfileDraft {
  return {
    owner_name: nullable(draft.owner_name),
    business_name: nullable(draft.business_name),
    business_type: nullable(draft.business_type),
    public_description: nullable(draft.public_description),
    timezone: nullable(draft.timezone),
    business_hours: draft.business_hours,
    existing_phone_e164: draft.existing_phone_e164 ? normalizeFrenchNumber(draft.existing_phone_e164) : null,
    confirmed_carrier: draft.confirmed_carrier ?? null,
    receptionist_name: nullable(draft.receptionist_name),
    faqs: (draft.faqs ?? []).map((faq) => ({ question: faq.question.trim(), answer: faq.answer.trim() })),
    special_instructions: nullable(draft.special_instructions),
    escalation_notes: nullable(draft.escalation_notes),
  };
}

function syntacticallyValid(draft: BusinessProfileDraft, snapshot: ActivationSnapshot): boolean {
  const constraints = snapshot.profile_constraints;
  const within = (value: string | null | undefined, maximum: number) => (value?.length ?? 0) <= maximum;
  if (!within(draft.owner_name, constraints.name_max_length)) return false;
  if (!within(draft.business_name, constraints.name_max_length)) return false;
  if (!within(draft.business_type, constraints.business_type_max_length)) return false;
  if (!within(draft.receptionist_name, constraints.name_max_length)) return false;
  if (!within(draft.public_description, constraints.public_description_max_length)) return false;
  if (!within(draft.special_instructions, constraints.special_instructions_max_length)) return false;
  if (!within(draft.escalation_notes, constraints.escalation_notes_max_length)) return false;
  if (draft.business_hours && validateBusinessHours(draft.business_hours)) return false;
  if (draft.existing_phone_e164 && !normalizeFrenchNumber(draft.existing_phone_e164)) return false;
  if ((draft.faqs?.length ?? 0) > constraints.faq_max_items) return false;
  return (draft.faqs ?? []).every(
    (faq) =>
      faq.question.trim().length > 0 &&
      faq.answer.trim().length > 0 &&
      faq.question.length <= constraints.faq_question_max_length &&
      faq.answer.length <= constraints.faq_answer_max_length,
  );
}

const STATUS_COPY = {
  unsaved: "Unsaved",
  saving: "Saving…",
  saved: "Saved",
  error: "Couldn't save",
} as const;

export function ProfileForm({ snapshot, milestone }: ProfileFormProps) {
  const router = useRouter();
  const form = useForm<BusinessProfileDraft>({
    defaultValues: initialDraft(snapshot),
    mode: "onChange",
    shouldFocusError: true,
  });
  const watchedDraft = form.watch();
  const draft = useMemo(() => actionDraft(watchedDraft), [watchedDraft]);
  const valid = syntacticallyValid(watchedDraft, snapshot);
  const autosave = useProfileAutosave({ draft, dirty: form.formState.isDirty, valid });
  const [actionError, setActionError] = useState<string | null>(null);
  const [continuing, setContinuing] = useState(false);

  const saveBeforeLookup = async () => {
    const result = await autosave.flush();
    return result.status === "success";
  };

  const requireBusinessFields = (): boolean => {
    const values = form.getValues();
    const required: Array<["owner_name" | "business_name" | "business_type", string]> = [
      ["owner_name", "Add the name Presvo should use for you."],
      ["business_name", "Add the business name callers know."],
      ["business_type", "Add a short business type."],
    ];
    for (const [name, message] of required) {
      if (!values[name]?.trim()) {
        form.setError(name, { type: "required", message }, { shouldFocus: true });
        return false;
      }
    }
    if (!values.timezone) {
      form.setError("timezone", { type: "required", message: "Confirm the timezone." }, { shouldFocus: true });
      return false;
    }
    if (!values.business_hours || validateBusinessHours(values.business_hours)) {
      form.setError("business_hours", { type: "validate", message: "Review the opening hours." });
      return false;
    }
    if (!values.existing_phone_e164 || !normalizeFrenchNumber(values.existing_phone_e164)) {
      form.setError(
        "existing_phone_e164",
        { type: "validate", message: "Enter a valid French number." },
        { shouldFocus: true },
      );
      return false;
    }
    if (!values.confirmed_carrier) {
      form.setError("confirmed_carrier", { type: "required", message: "Confirm or choose the carrier." });
      form.setFocus("existing_phone_e164");
      return false;
    }
    return true;
  };

  const requireReceptionistFields = (): boolean => {
    const values = form.getValues();
    if (!values.receptionist_name?.trim()) {
      form.setError(
        "receptionist_name",
        { type: "required", message: "Add the receptionist name." },
        { shouldFocus: true },
      );
      return false;
    }
    if (!values.public_description?.trim()) {
      form.setError(
        "public_description",
        { type: "required", message: "Describe the business for callers." },
        { shouldFocus: true },
      );
      return false;
    }
    const incompleteFaq = (values.faqs ?? []).findIndex((faq) => !faq.question.trim() || !faq.answer.trim());
    if (incompleteFaq >= 0) {
      const field = values.faqs?.[incompleteFaq]?.question.trim() ? "answer" : "question";
      form.setError(
        `faqs.${incompleteFaq}.${field}`,
        { type: "required", message: "Complete this FAQ." },
        { shouldFocus: true },
      );
      return false;
    }
    return true;
  };

  const continueJourney = async () => {
    setActionError(null);
    const milestoneValid = milestone === "business" ? requireBusinessFields() : requireReceptionistFields();
    if (!milestoneValid || !valid) return;
    setContinuing(true);
    const saved = await autosave.flush();
    if (saved.status === "error") {
      setActionError(saved.message);
      setContinuing(false);
      return;
    }
    if (milestone === "business") {
      router.push("/activate?milestone=receptionist");
      return;
    }
    const confirmed = await confirmProfileAction({});
    if (confirmed.status === "error") {
      setActionError(confirmed.message);
      setContinuing(false);
      if (confirmed.code === "profile_incomplete" && confirmed.fields?.length) {
        const firstField = confirmed.fields[0] as keyof BusinessProfileDraft;
        form.setError(firstField, { type: "server", message: confirmed.message }, { shouldFocus: true });
      }
      return;
    }
    router.refresh();
  };

  return (
    <form className="flex flex-col gap-8" onSubmit={form.handleSubmit(continueJourney)} noValidate>
      <div className="flex flex-wrap items-center justify-between gap-3 border-y py-3">
        <p className="text-muted-foreground text-sm">Changes are saved as a complete draft.</p>
        <p className="font-medium text-sm" aria-live="polite" data-status={autosave.status}>
          {STATUS_COPY[autosave.status]}
          {autosave.status === "error" && autosave.message ? (
            <span className="ml-2 font-normal text-destructive">{autosave.message}</span>
          ) : null}
        </p>
      </div>

      {milestone === "business" ? (
        <BusinessFields
          form={form}
          constraints={snapshot.profile_constraints}
          detectedCarrier={snapshot.profile.detected_carrier}
          carrierLookupStatus={snapshot.profile.carrier_lookup_status}
          onSaveBeforeLookup={saveBeforeLookup}
        />
      ) : (
        <ReceptionistFields form={form} constraints={snapshot.profile_constraints} />
      )}

      {actionError ? (
        <Alert variant="destructive">
          <AlertTitle>We couldn't continue</AlertTitle>
          <AlertDescription>{actionError}</AlertDescription>
        </Alert>
      ) : null}

      <div className="flex flex-wrap items-center justify-between gap-3 border-t pt-6">
        <p className="max-w-xl text-muted-foreground text-sm">
          {milestone === "business"
            ? "Next, shape what your receptionist knows and says."
            : "Confirmation locks this exact saved revision before number setup."}
        </p>
        <Button type="submit" size="lg" disabled={continuing}>
          {continuing ? "Saving…" : "Continue"}
        </Button>
      </div>
    </form>
  );
}
