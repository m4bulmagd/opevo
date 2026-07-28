"use client";

import { useState, useTransition } from "react";

import { Check, TriangleAlert } from "lucide-react";
import { toast } from "sonner";

import { type AgentActionResult, saveAgentSettingsAction } from "@/app/(app)/dashboard/agent/actions";
import { type ActionPhase, ActionState } from "@/components/motion/action-state";
import { PresvoMotionProvider } from "@/components/motion/presvo-motion-provider";
import { SettingsSection } from "@/components/product/settings-section";
import { Button } from "@/components/ui/button";
import { Field, FieldContent, FieldDescription, FieldGroup, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Spinner } from "@/components/ui/spinner";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import type { AgentConfig } from "@/lib/types/agent";

export function AgentSettingsForm({
  initialConfig,
  readOnly = false,
}: {
  initialConfig: AgentConfig;
  readOnly?: boolean;
}) {
  const [formState, setFormState] = useState(initialConfig);
  const [result, setResult] = useState<AgentActionResult | null>(null);
  const [isPending, startTransition] = useTransition();

  const onSave = () => {
    if (readOnly) {
      return;
    }

    setResult(null);
    startTransition(async () => {
      const nextResult = await saveAgentSettingsAction({
        ...formState,
        owner_context: formState.owner_context?.trim() ? formState.owner_context : null,
      });

      setResult(nextResult);

      if (nextResult.status === "success") {
        setFormState(nextResult.config ?? formState);
        toast.success(nextResult.message);
      } else {
        toast.error(nextResult.message);
      }
    });
  };

  const actionPhase: ActionPhase = isPending ? "pending" : (result?.status ?? "idle");
  const updateFormState = (updates: Partial<typeof formState>) => {
    setResult(null);
    setFormState((current) => ({ ...current, ...updates }));
  };

  return (
    <div className="rounded-lg border border-border/80 bg-surface px-4 py-5 shadow-raised sm:px-6 sm:py-6">
      <p className="mb-6 max-w-2xl text-sm text-text-secondary leading-relaxed">
        {readOnly
          ? "These saved settings are read-only while the account is deactivating or inactive."
          : "Update the public identity, knowledge, and routing state for your assistant."}
      </p>

      <div className="flex flex-col">
        <SettingsSection
          description="Choose the name shown in Presvo and used as your receptionist's identity."
          title="Identity"
        >
          <FieldGroup>
            <Field>
              <FieldLabel htmlFor="agent_name">Agent name</FieldLabel>
              <FieldContent>
                <Input
                  disabled={readOnly}
                  id="agent_name"
                  name="agent_name"
                  onChange={(event) => updateFormState({ agent_name: event.target.value })}
                  value={formState.agent_name}
                />
                <FieldDescription>
                  Shown as the assistant identity in the dashboard and during call handling.
                </FieldDescription>
              </FieldContent>
            </Field>
          </FieldGroup>
        </SettingsSection>

        <SettingsSection
          description="Control whether the saved configuration permits calls to be routed to this receptionist."
          title="Call handling"
        >
          <FieldGroup>
            <Field orientation="responsive">
              <FieldContent>
                <FieldLabel htmlFor="is_enabled">Enable call routing</FieldLabel>
                <FieldDescription>
                  Billing, number assignment, and setup must be complete before routing can go live.
                </FieldDescription>
              </FieldContent>
              <Switch
                checked={formState.is_enabled}
                disabled={readOnly}
                id="is_enabled"
                onCheckedChange={(checked) => updateFormState({ is_enabled: checked })}
              />
            </Field>
          </FieldGroup>
        </SettingsSection>

        <SettingsSection
          description="Give the receptionist concise context about the business or person it represents."
          title="Business context"
        >
          <FieldGroup>
            <Field>
              <FieldLabel htmlFor="owner_context">Owner context</FieldLabel>
              <FieldContent>
                <Textarea
                  disabled={readOnly}
                  id="owner_context"
                  name="owner_context"
                  onChange={(event) => updateFormState({ owner_context: event.target.value })}
                  value={formState.owner_context ?? ""}
                />
                <FieldDescription>Add lightweight business or operator context for the assistant.</FieldDescription>
              </FieldContent>
            </Field>
          </FieldGroup>
        </SettingsSection>

        <SettingsSection
          description="Maintain the instructions and operational facts the receptionist should use during calls."
          title="Instructions"
        >
          <FieldGroup>
            <Field>
              <FieldLabel htmlFor="system_prompt">System prompt</FieldLabel>
              <FieldContent>
                <Textarea
                  disabled={readOnly}
                  id="system_prompt"
                  name="system_prompt"
                  onChange={(event) => updateFormState({ system_prompt: event.target.value })}
                  value={formState.system_prompt}
                />
                <FieldDescription>Use explicit saves for prompt-heavy instructions.</FieldDescription>
              </FieldContent>
            </Field>

            <Field>
              <FieldLabel htmlFor="knowledge_base">Knowledge base</FieldLabel>
              <FieldContent>
                <Textarea
                  disabled={readOnly}
                  id="knowledge_base"
                  name="knowledge_base"
                  onChange={(event) => updateFormState({ knowledge_base: event.target.value })}
                  value={formState.knowledge_base}
                />
                <FieldDescription>Short operational facts the assistant should use during calls.</FieldDescription>
              </FieldContent>
            </Field>
          </FieldGroup>
        </SettingsSection>
      </div>

      <div className="mt-6 flex flex-col gap-4 border-border/80 border-t pt-5 sm:flex-row sm:items-center sm:justify-between">
        <p aria-atomic="true" aria-label="Save feedback" className="min-h-5 text-sm text-text-secondary" role="status">
          {result?.message}
        </p>
        <Button className="min-h-11 px-4 sm:ml-auto" disabled={readOnly || isPending} onClick={onSave}>
          <PresvoMotionProvider>
            <ActionState
              error={
                <>
                  <TriangleAlert aria-hidden data-icon="inline-start" />
                  Try saving again
                </>
              }
              idle="Save agent settings"
              pending={
                <>
                  <Spinner data-icon="inline-start" />
                  Saving settings
                </>
              }
              phase={actionPhase}
              success={
                <>
                  <Check aria-hidden data-icon="inline-start" />
                  Settings saved
                </>
              }
            />
          </PresvoMotionProvider>
        </Button>
      </div>
    </div>
  );
}
