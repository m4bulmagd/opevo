"use client";

import { useState } from "react";

import { PhoneCall, RotateCcw } from "lucide-react";

import { TestAssistantPreview } from "@/components/agent/test-assistant-preview";
import { type PreviewVoice, VoicePreviewSelector } from "@/components/agent/voice-preview-selector";
import { CapabilityBadge } from "@/components/product/capability-badge";
import { ProductSurface } from "@/components/product/product-surface";
import { Button } from "@/components/ui/button";
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select";

type PreviewPersonality = "friendly" | "professional" | "concise" | "warm";

type PreviewSettings = {
  language: string;
  personality: PreviewPersonality;
  responseStyle: string;
  speakingSpeed: number;
  provider: string;
  voiceId: string;
};

const INITIAL_SETTINGS: PreviewSettings = {
  language: "fr-FR",
  personality: "professional",
  responseStyle: "balanced",
  speakingSpeed: 1,
  provider: "presvo-balanced",
  voiceId: "camille",
};

const PERSONALITIES: ReadonlyArray<{
  value: PreviewPersonality;
  label: string;
  hint: string;
}> = [
  { value: "friendly", label: "Friendly", hint: "Conversational and approachable" },
  { value: "professional", label: "Professional", hint: "Clear, composed, and precise" },
  { value: "concise", label: "Concise", hint: "Short answers with direct next steps" },
  { value: "warm", label: "Warm", hint: "Patient and reassuring" },
];

export function AssistantPreview({ agentName }: { agentName: string }) {
  const [settings, setSettings] = useState(INITIAL_SETTINGS);
  const [voiceStatus, setVoiceStatus] = useState("");
  const [testOpen, setTestOpen] = useState(false);

  const update = (values: Partial<PreviewSettings>) => {
    setSettings((current) => ({ ...current, ...values }));
  };

  const resetPreview = () => {
    setSettings(INITIAL_SETTINGS);
    setVoiceStatus("");
    setTestOpen(false);
  };

  const previewVoice = (voice: PreviewVoice) => {
    setVoiceStatus(`Previewing ${voice.name} locally — no provider request was made.`);
  };

  return (
    <section aria-label="Advanced assistant Preview">
      <ProductSurface
        action={<CapabilityBadge status="preview" />}
        description="Explore planned assistant controls with fictional France-first data. Changes remain in this browser tab and reset on reload."
        title="Advanced assistant"
      >
        <div className="space-y-7">
          <fieldset>
            <legend className="font-medium text-sm text-text-primary">Personality</legend>
            <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              {PERSONALITIES.map((option) => {
                const selected = settings.personality === option.value;
                return (
                  <label
                    className={
                      selected
                        ? "cursor-pointer rounded-xl border border-primary bg-primary-soft/60 p-3 ring-1 ring-primary/30"
                        : "cursor-pointer rounded-xl border border-border bg-card p-3 hover:bg-muted/50"
                    }
                    key={option.value}
                  >
                    <span className="flex items-start gap-2">
                      <input
                        checked={selected}
                        className="mt-0.5 size-4 accent-primary"
                        name="preview-personality"
                        onChange={() => update({ personality: option.value })}
                        type="radio"
                        value={option.value}
                      />
                      <span>
                        <span className="block font-medium text-sm">{option.label}</span>
                        <span className="mt-1 block text-text-secondary text-xs">{option.hint}</span>
                      </span>
                    </span>
                  </label>
                );
              })}
            </div>
          </fieldset>

          <div className="grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
            <label className="grid gap-2 font-medium text-sm" htmlFor="preview-response-style">
              Response style
              <NativeSelect
                className="w-full [&_select]:min-h-11"
                id="preview-response-style"
                name="preview-response-style"
                onChange={(event) => update({ responseStyle: event.target.value })}
                value={settings.responseStyle}
              >
                <NativeSelectOption value="brief">Brief</NativeSelectOption>
                <NativeSelectOption value="balanced">Balanced</NativeSelectOption>
                <NativeSelectOption value="detailed">Detailed</NativeSelectOption>
              </NativeSelect>
            </label>

            <label className="grid gap-2 font-medium text-sm" htmlFor="preview-language">
              Language
              <NativeSelect
                aria-label="Preview language"
                className="w-full [&_select]:min-h-11"
                id="preview-language"
                name="preview-language"
                onChange={(event) => update({ language: event.target.value })}
                value={settings.language}
              >
                <NativeSelectOption value="fr-FR">Français (France)</NativeSelectOption>
                <NativeSelectOption value="fr-BE">Français (Belgique)</NativeSelectOption>
                <NativeSelectOption value="en-GB">English (United Kingdom)</NativeSelectOption>
              </NativeSelect>
            </label>

            <label className="grid gap-2 font-medium text-sm" htmlFor="preview-provider">
              Provider profile
              <NativeSelect
                className="w-full [&_select]:min-h-11"
                id="preview-provider"
                name="preview-provider"
                onChange={(event) => update({ provider: event.target.value })}
                value={settings.provider}
              >
                <NativeSelectOption value="presvo-fast">Presvo Fast · lower latency</NativeSelectOption>
                <NativeSelectOption value="presvo-balanced">Presvo Balanced</NativeSelectOption>
                <NativeSelectOption value="presvo-reasoning">Presvo Reasoning · complex calls</NativeSelectOption>
              </NativeSelect>
            </label>
          </div>

          <label className="grid max-w-md gap-2 font-medium text-sm" htmlFor="preview-speed">
            Speaking speed · {settings.speakingSpeed.toFixed(2)}×
            <input
              aria-label="Preview speaking speed"
              className="h-11 w-full accent-primary"
              id="preview-speed"
              max="1.4"
              min="0.8"
              name="preview-speaking-speed"
              onChange={(event) => update({ speakingSpeed: Number(event.target.value) })}
              step="0.05"
              type="range"
              value={settings.speakingSpeed}
            />
          </label>

          <div>
            <div className="mb-4">
              <h3 className="font-semibold text-sm">Voice</h3>
              <p className="mt-1 text-text-secondary text-xs">
                These samples are interface previews; no audio or voice provider is contacted.
              </p>
            </div>
            <VoicePreviewSelector
              onChange={(voiceId) => update({ voiceId })}
              onPreview={previewVoice}
              value={settings.voiceId}
            />
            <p aria-label="Voice preview status" className="mt-3 min-h-5 text-success text-xs" role="status">
              {voiceStatus}
            </p>
          </div>

          <div className="flex flex-col gap-3 border-border border-t pt-5 sm:flex-row sm:items-center sm:justify-between">
            <p className="max-w-xl text-text-secondary text-xs">
              Preview controls do not alter {agentName}&apos;s saved live configuration.
            </p>
            <div className="flex flex-wrap gap-2">
              <Button className="min-h-11" onClick={resetPreview} variant="ghost">
                <RotateCcw aria-hidden data-icon="inline-start" />
                Reset Preview settings
              </Button>
              <Button className="min-h-11" onClick={() => setTestOpen(true)}>
                <PhoneCall aria-hidden data-icon="inline-start" />
                Test assistant Preview
              </Button>
            </div>
          </div>
        </div>
      </ProductSurface>

      <TestAssistantPreview agentName={agentName} onOpenChange={setTestOpen} open={testOpen} />
    </section>
  );
}
