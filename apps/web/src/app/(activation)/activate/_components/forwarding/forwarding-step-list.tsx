"use client";

import { useSyncExternalStore } from "react";

import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from "@/components/ui/accordion";
import type { ForwardingGuide, ForwardingStep } from "@/lib/types/activation";

import { CopyDialCode } from "./copy-dial-code";

type ForwardingStepListProps = {
  guide: ForwardingGuide;
  onCopyResult: (message: string) => void;
};

const MOBILE_QUERY = "(max-width: 767px)";

function subscribeToMobileQuery(onChange: () => void): () => void {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") return () => undefined;
  const query = window.matchMedia(MOBILE_QUERY);
  query.addEventListener("change", onChange);
  return () => query.removeEventListener("change", onChange);
}

function getMobileSnapshot(): boolean {
  return typeof window !== "undefined" && typeof window.matchMedia === "function"
    ? window.matchMedia(MOBILE_QUERY).matches
    : false;
}

function StepDetails({ step, onCopyResult }: { step: ForwardingStep; onCopyResult: (message: string) => void }) {
  return (
    <div className="flex flex-col gap-4">
      <ol className="list-decimal space-y-2 pl-5 text-muted-foreground leading-6">
        {step.instructions.map((instruction) => (
          <li key={instruction}>{instruction}</li>
        ))}
      </ol>

      {step.dial_code ? (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border bg-muted/30 p-3">
          <div>
            <p className="text-muted-foreground text-xs">Dial code</p>
            <code className="font-semibold text-base tabular-nums">{step.dial_code}</code>
          </div>
          <CopyDialCode code={step.dial_code} label={step.title} onResult={onCopyResult} />
        </div>
      ) : null}

      {step.disable_code ? (
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <p className="text-muted-foreground text-xs">Turn off this condition</p>
            <code className="font-medium tabular-nums">{step.disable_code}</code>
          </div>
        </div>
      ) : null}
    </div>
  );
}

export function ForwardingStepList({ guide, onCopyResult }: ForwardingStepListProps) {
  const mobile = useSyncExternalStore(subscribeToMobileQuery, getMobileSnapshot, () => false);
  const sources = Array.from(
    new Set(guide.steps.map((step) => step.source_url).filter((source): source is string => source !== null)),
  );

  return (
    <div className="flex flex-col gap-5">
      {mobile ? (
        <Accordion type="multiple" defaultValue={[guide.steps[0]?.condition ?? ""]}>
          {guide.steps.map((step) => (
            <AccordionItem key={step.condition} value={step.condition}>
              <AccordionTrigger>{step.title}</AccordionTrigger>
              <AccordionContent>
                <StepDetails step={step} onCopyResult={onCopyResult} />
              </AccordionContent>
            </AccordionItem>
          ))}
        </Accordion>
      ) : (
        <dl className="divide-y border-y">
          {guide.steps.map((step) => (
            <div key={step.condition} className="grid gap-3 py-5 md:grid-cols-[11rem_1fr] md:gap-6">
              <dt className="font-semibold">{step.title}</dt>
              <dd>
                <StepDetails step={step} onCopyResult={onCopyResult} />
              </dd>
            </div>
          ))}
        </dl>
      )}

      {sources.length > 0 ? (
        <details className="text-sm">
          <summary className="cursor-pointer font-medium">Carrier guidance source</summary>
          <div className="mt-3 flex flex-col items-start gap-2 text-muted-foreground">
            {sources.map((source, index) => (
              <a
                key={source}
                className="underline underline-offset-4 hover:text-foreground"
                href={source}
                target="_blank"
                rel="noreferrer"
                aria-label={sources.length === 1 ? "Official carrier source" : `Official carrier source ${index + 1}`}
              >
                Official carrier source{sources.length === 1 ? "" : ` ${index + 1}`}
              </a>
            ))}
          </div>
        </details>
      ) : null}
    </div>
  );
}
