"use client";

import { type UseFormReturn, useFieldArray } from "react-hook-form";

import { Button } from "@/components/ui/button";
import { Field, FieldDescription, FieldError, FieldGroup, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import type { BusinessProfileConstraints, BusinessProfileDraft } from "@/lib/types/activation";

import { ReceptionistPreview } from "./receptionist-preview";

type ReceptionistFieldsProps = {
  form: UseFormReturn<BusinessProfileDraft>;
  constraints: BusinessProfileConstraints;
};

const count = (value: string | null | undefined) => value?.length ?? 0;

export function ReceptionistFields({ form, constraints }: ReceptionistFieldsProps) {
  const { fields, append, remove } = useFieldArray({ control: form.control, name: "faqs" });
  const draft = form.watch();
  const errors = form.formState.errors;

  return (
    <div className="grid items-start gap-8 lg:grid-cols-[minmax(0,1fr)_minmax(18rem,0.72fr)]">
      <FieldGroup>
        <Field data-invalid={Boolean(errors.receptionist_name)}>
          <FieldLabel htmlFor="receptionist-name">Receptionist name</FieldLabel>
          <Input
            autoComplete="off"
            id="receptionist-name"
            maxLength={constraints.name_max_length}
            aria-invalid={Boolean(errors.receptionist_name)}
            {...form.register("receptionist_name", { required: "Add the name callers should hear." })}
          />
          <FieldDescription>
            {count(draft.receptionist_name)} / {constraints.name_max_length}
          </FieldDescription>
          <FieldError errors={[errors.receptionist_name]} />
        </Field>

        <Field data-invalid={Boolean(errors.public_description)}>
          <FieldLabel htmlFor="public-description">Public description</FieldLabel>
          <Textarea
            autoComplete="off"
            id="public-description"
            maxLength={constraints.public_description_max_length}
            rows={5}
            aria-invalid={Boolean(errors.public_description)}
            {...form.register("public_description", { required: "Describe the business for callers." })}
          />
          <FieldDescription>
            {count(draft.public_description)} / {constraints.public_description_max_length}
          </FieldDescription>
          <FieldError errors={[errors.public_description]} />
        </Field>

        <div className="flex flex-col gap-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 className="font-medium">Frequently asked questions</h2>
              <p className="text-muted-foreground text-sm">
                Give short, dependable answers to common caller questions.
              </p>
            </div>
            {fields.length < constraints.faq_max_items ? (
              <Button type="button" variant="outline" onClick={() => append({ question: "", answer: "" })}>
                Add FAQ
              </Button>
            ) : null}
          </div>
          {fields.map((field, index) => (
            <div className="flex flex-col gap-4 rounded-xl border p-4" key={field.id}>
              <Field data-invalid={Boolean(errors.faqs?.[index]?.question)}>
                <FieldLabel htmlFor={`faq-${index}-question`}>FAQ question {index + 1}</FieldLabel>
                <Input
                  autoComplete="off"
                  id={`faq-${index}-question`}
                  maxLength={constraints.faq_question_max_length}
                  aria-invalid={Boolean(errors.faqs?.[index]?.question)}
                  {...form.register(`faqs.${index}.question`, { required: "Add an FAQ question." })}
                />
                <FieldDescription>
                  {count(draft.faqs?.[index]?.question)} / {constraints.faq_question_max_length}
                </FieldDescription>
                <FieldError errors={[errors.faqs?.[index]?.question]} />
              </Field>
              <Field data-invalid={Boolean(errors.faqs?.[index]?.answer)}>
                <FieldLabel htmlFor={`faq-${index}-answer`}>FAQ answer {index + 1}</FieldLabel>
                <Textarea
                  autoComplete="off"
                  id={`faq-${index}-answer`}
                  maxLength={constraints.faq_answer_max_length}
                  aria-invalid={Boolean(errors.faqs?.[index]?.answer)}
                  {...form.register(`faqs.${index}.answer`, { required: "Add an FAQ answer." })}
                />
                <FieldDescription>
                  {count(draft.faqs?.[index]?.answer)} / {constraints.faq_answer_max_length}
                </FieldDescription>
                <FieldError errors={[errors.faqs?.[index]?.answer]} />
              </Field>
              <Button type="button" variant="ghost" className="self-start" onClick={() => remove(index)}>
                Remove FAQ {index + 1}
              </Button>
            </div>
          ))}
        </div>

        <Field data-invalid={Boolean(errors.special_instructions)}>
          <FieldLabel htmlFor="special-instructions">Special instructions</FieldLabel>
          <Textarea
            autoComplete="off"
            id="special-instructions"
            maxLength={constraints.special_instructions_max_length}
            rows={4}
            aria-invalid={Boolean(errors.special_instructions)}
            {...form.register("special_instructions")}
          />
          <FieldDescription>
            {count(draft.special_instructions)} / {constraints.special_instructions_max_length}
          </FieldDescription>
          <FieldError errors={[errors.special_instructions]} />
        </Field>

        <Field data-invalid={Boolean(errors.escalation_notes)}>
          <FieldLabel htmlFor="escalation-notes">Escalation notes</FieldLabel>
          <Textarea
            autoComplete="off"
            id="escalation-notes"
            maxLength={constraints.escalation_notes_max_length}
            rows={4}
            aria-invalid={Boolean(errors.escalation_notes)}
            {...form.register("escalation_notes")}
          />
          <FieldDescription>
            {count(draft.escalation_notes)} / {constraints.escalation_notes_max_length}
          </FieldDescription>
          <FieldError errors={[errors.escalation_notes]} />
        </Field>
      </FieldGroup>
      <ReceptionistPreview draft={draft} />
    </div>
  );
}
