"use client";

import { useState, useTransition } from "react";

import { toast } from "sonner";

import { type AgentActionResult, saveAgentSettingsAction } from "@/app/(app)/dashboard/agent/actions";
import { UnsavedChangesBar } from "@/components/forms/unsaved-changes-bar";
import { SettingsSection } from "@/components/product/settings-section";
import { Field, FieldContent, FieldDescription, FieldGroup, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { useUnsavedChangesGuard } from "@/hooks/use-unsaved-changes-guard";
import type { AgentConfig } from "@/lib/types/agent";

export type AgentConfigurationTab = "general" | "instructions" | "knowledge";

function configKey(config: AgentConfig): string {
  return JSON.stringify(config);
}

export function AgentSettingsForm({
  initialConfig,
  readOnly = false,
  tab,
}: {
  initialConfig: AgentConfig;
  readOnly?: boolean;
  tab: AgentConfigurationTab;
}) {
  const [baseline, setBaseline] = useState(initialConfig);
  const [formState, setFormState] = useState(initialConfig);
  const [result, setResult] = useState<AgentActionResult | null>(null);
  const [isPending, startTransition] = useTransition();
  const dirty = configKey(formState) !== configKey(baseline);

  useUnsavedChangesGuard(dirty && !readOnly);

  const updateFormState = (updates: Partial<AgentConfig>) => {
    setResult(null);
    setFormState((current) => ({ ...current, ...updates }));
  };

  const discardDraft = () => {
    setFormState(baseline);
    setResult(null);
  };

  const saveDraft = () => {
    if (readOnly || !dirty) {
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
        const confirmedConfig = nextResult.config ?? formState;
        setBaseline(confirmedConfig);
        setFormState(confirmedConfig);
        toast.success(nextResult.message);
        return;
      }

      toast.error(nextResult.message);
    });
  };

  return (
    <div aria-labelledby={`agent-tab-${tab}`} id={`agent-panel-${tab}`} role="tabpanel">
      <div className="surface-card p-4 sm:p-6">
        <p className="mb-6 max-w-2xl text-sm text-text-secondary leading-relaxed">
          {readOnly
            ? "These saved settings are read-only while the account is deactivating or inactive."
            : "Live settings save through Opevo and apply to future calls after the backend confirms them."}
        </p>

        <div className="flex flex-col">
          {tab === "general" ? (
            <>
              <SettingsSection
                description="Choose the name shown in Opevo and used as your receptionist's identity."
                title="Identity"
              >
                <FieldGroup>
                  <Field>
                    <FieldLabel htmlFor="agent_name">Agent name</FieldLabel>
                    <FieldContent>
                      <Input
                        autoComplete="off"
                        className="min-h-11"
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
                  <Field className="rounded-xl border border-border bg-muted/50 p-4" orientation="responsive">
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
                        autoComplete="off"
                        className="min-h-28"
                        disabled={readOnly}
                        id="owner_context"
                        name="owner_context"
                        onChange={(event) => updateFormState({ owner_context: event.target.value })}
                        value={formState.owner_context ?? ""}
                      />
                      <FieldDescription>
                        Add lightweight business or operator context for the assistant.
                      </FieldDescription>
                    </FieldContent>
                  </Field>
                </FieldGroup>
              </SettingsSection>
            </>
          ) : null}

          {tab === "instructions" ? (
            <SettingsSection
              description="Define the boundaries and call-handling rules the receptionist must follow."
              title="Instructions"
            >
              <FieldGroup>
                <Field>
                  <FieldLabel htmlFor="system_prompt">System prompt</FieldLabel>
                  <FieldContent>
                    <Textarea
                      autoComplete="off"
                      className="min-h-64 font-mono text-xs leading-relaxed"
                      disabled={readOnly}
                      id="system_prompt"
                      name="system_prompt"
                      onChange={(event) => updateFormState({ system_prompt: event.target.value })}
                      value={formState.system_prompt}
                    />
                    <FieldDescription>
                      Use explicit, operational instructions. Changes are applied only after a confirmed save.
                    </FieldDescription>
                  </FieldContent>
                </Field>
              </FieldGroup>
            </SettingsSection>
          ) : null}

          {tab === "knowledge" ? (
            <SettingsSection
              description="Maintain the business facts the receptionist may use while answering callers."
              title="Knowledge base"
            >
              <FieldGroup>
                <Field>
                  <FieldLabel htmlFor="knowledge_base">Knowledge base</FieldLabel>
                  <FieldContent>
                    <Textarea
                      autoComplete="off"
                      className="min-h-72 text-sm leading-relaxed"
                      disabled={readOnly}
                      id="knowledge_base"
                      name="knowledge_base"
                      onChange={(event) => updateFormState({ knowledge_base: event.target.value })}
                      value={formState.knowledge_base}
                    />
                    <FieldDescription>
                      Store only accurate, customer-safe facts. Do not include private credentials or secrets.
                    </FieldDescription>
                  </FieldContent>
                </Field>
              </FieldGroup>
            </SettingsSection>
          ) : null}
        </div>

        <p
          aria-atomic="true"
          aria-label="Save feedback"
          className={
            result?.status === "error" ? "mt-5 min-h-5 text-destructive text-sm" : "mt-5 min-h-5 text-sm text-success"
          }
          role="status"
        >
          {result?.message}
        </p>
      </div>

      <UnsavedChangesBar
        dirty={dirty && !readOnly}
        feedback={result?.status === "error" ? result.message : null}
        onDiscard={discardDraft}
        onSave={saveDraft}
        pending={isPending}
      />
    </div>
  );
}
