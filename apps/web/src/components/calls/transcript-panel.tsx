"use client";

import { useMemo, useState } from "react";

import { Search } from "lucide-react";

import { Input } from "@/components/ui/input";
import type { CallTranscriptLine } from "@/lib/types/calls";
import { cn } from "@/lib/utils";

function highlightedText(text: string, search: string) {
  const term = search.trim();
  if (!term) return text;

  const index = text.toLocaleLowerCase().indexOf(term.toLocaleLowerCase());
  if (index === -1) return text;

  return (
    <>
      {text.slice(0, index)}
      <mark className="rounded bg-warning/40 px-0.5 text-foreground">{text.slice(index, index + term.length)}</mark>
      {text.slice(index + term.length)}
    </>
  );
}

export function TranscriptPanel({ transcript }: { transcript: CallTranscriptLine[] }) {
  const [search, setSearch] = useState("");
  const visibleTranscript = useMemo(() => {
    const term = search.trim().toLocaleLowerCase();
    if (!term) return transcript;
    return transcript.filter((line) => line.text.toLocaleLowerCase().includes(term));
  }, [search, transcript]);

  if (transcript.length === 0) {
    return <p className="text-sm text-text-secondary">No transcript is available for this call.</p>;
  }

  return (
    <div className="space-y-5">
      <div className="relative ml-auto sm:max-w-72">
        <Search
          aria-hidden
          className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground"
        />
        <Input
          aria-label="Search transcript"
          autoComplete="off"
          className="min-h-11 pl-9"
          name="transcript-search"
          onChange={(event) => setSearch(event.target.value)}
          placeholder="Search transcript"
          type="search"
          value={search}
        />
      </div>
      {visibleTranscript.length === 0 ? (
        <p className="py-6 text-center text-sm text-text-secondary">No transcript lines match “{search}”.</p>
      ) : (
        <ol className="space-y-4">
          {visibleTranscript.map((line) => {
            const isAssistant = line.speaker.toLocaleLowerCase() === "assistant";
            return (
              <li
                className={cn("flex gap-3", isAssistant ? "flex-row" : "flex-row-reverse")}
                key={`${line.sequence_number}-${line.created_at}`}
              >
                <span
                  aria-hidden
                  className={cn(
                    "mt-1 grid size-8 shrink-0 place-items-center rounded-full font-semibold text-[11px]",
                    isAssistant ? "bg-primary text-primary-foreground" : "bg-secondary text-secondary-foreground",
                  )}
                >
                  {isAssistant ? "AI" : "CA"}
                </span>
                <div className={cn("min-w-0 max-w-[85%]", isAssistant ? "text-left" : "text-right")}>
                  <p className="mb-1 font-medium text-[11px] text-text-tertiary uppercase tracking-wide">
                    {line.speaker}
                  </p>
                  <p
                    className={cn(
                      "inline-block break-words rounded-xl px-3.5 py-2.5 text-sm leading-relaxed",
                      isAssistant
                        ? "rounded-tl-sm bg-primary-soft text-accent-foreground"
                        : "rounded-tr-sm bg-muted text-foreground",
                    )}
                  >
                    {highlightedText(line.text, search)}
                  </p>
                </div>
              </li>
            );
          })}
        </ol>
      )}
    </div>
  );
}
