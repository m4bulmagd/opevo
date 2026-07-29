"use client";

import { Play, Volume2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export type PreviewVoice = {
  id: string;
  name: string;
  description: string;
  accent: string;
};

export const PREVIEW_VOICES: readonly PreviewVoice[] = [
  {
    id: "camille",
    name: "Camille",
    description: "Claire et chaleureuse, adaptée aux accueils professionnels.",
    accent: "France · voix féminine",
  },
  {
    id: "louis",
    name: "Louis",
    description: "Posée et précise, avec un rythme naturel.",
    accent: "France · voix masculine",
  },
  {
    id: "ines",
    name: "Inès",
    description: "Dynamique et rassurante pour les demandes du quotidien.",
    accent: "France · voix féminine",
  },
  {
    id: "noa",
    name: "Noa",
    description: "Neutre et articulée pour une grande variété de secteurs.",
    accent: "France · voix neutre",
  },
] as const;

type VoicePreviewSelectorProps = {
  onChange: (voiceId: string) => void;
  onPreview: (voice: PreviewVoice) => void;
  value: string;
};

export function VoicePreviewSelector({ onChange, onPreview, value }: VoicePreviewSelectorProps) {
  return (
    <fieldset>
      <legend className="sr-only">Preview assistant voice</legend>
      <div aria-label="Preview assistant voice" className="grid gap-3 sm:grid-cols-2" role="radiogroup">
        {PREVIEW_VOICES.map((voice) => {
          const selected = voice.id === value;
          return (
            <div
              className={cn(
                "rounded-xl border bg-card p-4 transition-[background-color,border-color,box-shadow]",
                selected ? "border-primary bg-primary-soft/60 ring-1 ring-primary/30" : "border-border",
              )}
              key={voice.id}
            >
              <label className="flex cursor-pointer items-start gap-3">
                <input
                  checked={selected}
                  className="mt-1 size-4 accent-primary"
                  name="preview-voice"
                  onChange={() => onChange(voice.id)}
                  type="radio"
                  value={voice.id}
                />
                <span className="min-w-0">
                  <span className="flex items-center gap-2 font-semibold text-sm">
                    <Volume2 aria-hidden className="size-4 text-text-tertiary" />
                    {voice.name}
                  </span>
                  <span className="mt-1 block text-text-secondary text-xs leading-relaxed">{voice.description}</span>
                  <span className="mt-2 block font-medium text-[11px] text-text-tertiary uppercase tracking-wide">
                    {voice.accent}
                  </span>
                </span>
              </label>
              <Button
                aria-label={`Preview ${voice.name} locally`}
                className="mt-3 min-h-11"
                onClick={() => onPreview(voice)}
                size="sm"
                variant="outline"
              >
                <Play aria-hidden data-icon="inline-start" />
                Preview voice
              </Button>
            </div>
          );
        })}
      </div>
    </fieldset>
  );
}
