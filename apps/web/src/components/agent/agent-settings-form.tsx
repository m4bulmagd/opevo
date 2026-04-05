"use client";

import { useState, useTransition } from "react";

import { toast } from "sonner";

import { saveAgentSettingsAction } from "@/app/(app)/dashboard/agent/actions";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Field, FieldContent, FieldDescription, FieldGroup, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Spinner } from "@/components/ui/spinner";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import type { AgentConfig, PipelineMode } from "@/lib/types/agent";

export function AgentSettingsForm({ initialConfig }: { initialConfig: AgentConfig }) {
  const [formState, setFormState] = useState(initialConfig);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [isPending, startTransition] = useTransition();

  const onSave = () => {
    startTransition(async () => {
      const result = await saveAgentSettingsAction({
        ...formState,
        owner_context: formState.owner_context?.trim() ? formState.owner_context : null,
      });

      setFeedback(result.message);

      if (result.status === "success") {
        setFormState(result.config ?? formState);
        toast.success(result.message);
      } else {
        toast.error(result.message);
      }
    });
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>Agent settings</CardTitle>
        <CardDescription>Update the public identity, knowledge, and routing state for your assistant.</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-6">
        <FieldGroup>
          <Field>
            <FieldLabel htmlFor="agent_name">Agent name</FieldLabel>
            <FieldContent>
              <Input
                id="agent_name"
                value={formState.agent_name}
                onChange={(event) => setFormState((current) => ({ ...current, agent_name: event.target.value }))}
              />
              <FieldDescription>
                Shown as the assistant identity in the dashboard and during call handling.
              </FieldDescription>
            </FieldContent>
          </Field>

          <Field>
            <FieldLabel htmlFor="owner_context">Owner context</FieldLabel>
            <FieldContent>
              <Textarea
                id="owner_context"
                value={formState.owner_context ?? ""}
                onChange={(event) => setFormState((current) => ({ ...current, owner_context: event.target.value }))}
              />
              <FieldDescription>Add lightweight business or operator context for the assistant.</FieldDescription>
            </FieldContent>
          </Field>

          <Field>
            <FieldLabel htmlFor="system_prompt">System prompt</FieldLabel>
            <FieldContent>
              <Textarea
                id="system_prompt"
                value={formState.system_prompt}
                onChange={(event) => setFormState((current) => ({ ...current, system_prompt: event.target.value }))}
              />
              <FieldDescription>Use explicit saves for prompt-heavy instructions.</FieldDescription>
            </FieldContent>
          </Field>

          <Field>
            <FieldLabel htmlFor="knowledge_base">Knowledge base</FieldLabel>
            <FieldContent>
              <Textarea
                id="knowledge_base"
                value={formState.knowledge_base}
                onChange={(event) => setFormState((current) => ({ ...current, knowledge_base: event.target.value }))}
              />
              <FieldDescription>Short operational facts the assistant should use during calls.</FieldDescription>
            </FieldContent>
          </Field>

          <Field>
            <FieldLabel>Pipeline mode</FieldLabel>
            <FieldContent>
              <ToggleGroup
                type="single"
                variant="outline"
                value={formState.pipeline_mode}
                onValueChange={(value) => {
                  if (!value) return;
                  setFormState((current) => ({ ...current, pipeline_mode: value as PipelineMode }));
                }}
              >
                <ToggleGroupItem value="stt_llm_tts">STT / LLM / TTS</ToggleGroupItem>
                <ToggleGroupItem value="sts">STS</ToggleGroupItem>
              </ToggleGroup>
              <FieldDescription>Choose the runtime pipeline used for live calls.</FieldDescription>
            </FieldContent>
          </Field>

          <Field orientation="responsive">
            <FieldContent>
              <FieldLabel htmlFor="is_enabled">Enable call routing</FieldLabel>
              <FieldDescription>
                This is operationally significant because the backend switches telephony routing immediately.
              </FieldDescription>
            </FieldContent>
            <Switch
              id="is_enabled"
              checked={formState.is_enabled}
              onCheckedChange={(checked) => setFormState((current) => ({ ...current, is_enabled: checked }))}
            />
          </Field>
        </FieldGroup>

        <div className="flex flex-col gap-3 border-t pt-6">
          {feedback ? <p className="text-muted-foreground text-sm">{feedback}</p> : null}
          <div className="flex justify-end">
            <Button onClick={onSave} disabled={isPending}>
              {isPending ? <Spinner data-icon="inline-start" /> : null}
              Save agent settings
            </Button>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
