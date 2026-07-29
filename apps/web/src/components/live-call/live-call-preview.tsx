"use client";

import { useEffect, useRef, useState } from "react";

import { CircleDot, Mic, NotebookPen, PhoneOff, RefreshCw, User2 } from "lucide-react";

import { CapabilityBadge } from "@/components/product/capability-badge";
import { PageIntro } from "@/components/product/page-intro";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";

type PreviewState = "connecting" | "active" | "completed" | "failed";
type PreviewMessage = {
  id: string;
  role: "assistant" | "caller";
  text: string;
};

const INITIAL_ELAPSED = 102;
const INITIAL_MESSAGES: PreviewMessage[] = [
  {
    id: "initial-assistant",
    role: "assistant",
    text: "Bonjour, vous êtes bien chez Atelier Marceau. Comment puis-je vous aider ?",
  },
  {
    id: "initial-caller",
    role: "caller",
    text: "Bonjour, je voudrais prendre rendez-vous pour découvrir votre nouveau showroom à Paris.",
  },
];
const PREVIEW_SCRIPT: Array<Omit<PreviewMessage, "id">> = [
  {
    role: "assistant",
    text: "Avec plaisir. Préférez-vous un rendez-vous jeudi matin ou jeudi après-midi ?",
  },
  {
    role: "caller",
    text: "Jeudi après-midi serait parfait, vers quinze heures si possible.",
  },
  {
    role: "assistant",
    text: "Je note votre préférence et l'équipe vous confirmera le créneau par SMS.",
  },
];
const PREVIEW_STATES: PreviewState[] = ["connecting", "active", "completed", "failed"];

const STATE_LABEL: Record<PreviewState, string> = {
  connecting: "Connecting",
  active: "Active",
  completed: "Completed",
  failed: "Failed",
};

function formatElapsed(totalSeconds: number) {
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

function PreviewTranscriptMessage({ message }: { message: PreviewMessage }) {
  const isAssistant = message.role === "assistant";
  const speaker = isAssistant ? "Camille" : "Sophie Bernard";

  return (
    <li className={cn("flex gap-3", isAssistant ? "flex-row" : "flex-row-reverse")}>
      <span
        aria-hidden
        className={cn(
          "mt-1 grid size-8 shrink-0 place-items-center rounded-full font-semibold text-[11px]",
          isAssistant ? "bg-primary text-primary-foreground" : "bg-secondary text-secondary-foreground",
        )}
      >
        {isAssistant ? "CA" : "SB"}
      </span>
      <div className={cn("min-w-0 max-w-[85%]", isAssistant ? "text-left" : "text-right")}>
        <p className="mb-1 font-medium text-[11px] text-muted-foreground">{speaker}</p>
        <p
          className={cn(
            "inline-block rounded-xl px-3.5 py-2.5 text-sm leading-relaxed",
            isAssistant
              ? "rounded-tl-sm bg-primary-soft text-accent-foreground"
              : "rounded-tr-sm bg-muted text-foreground",
          )}
        >
          {message.text}
        </p>
      </div>
    </li>
  );
}

export function LiveCallPreview() {
  const [state, setState] = useState<PreviewState>("active");
  const [elapsed, setElapsed] = useState(INITIAL_ELAPSED);
  const [messages, setMessages] = useState<PreviewMessage[]>(INITIAL_MESSAGES);
  const [notes, setNotes] = useState("");
  const [noteStatus, setNoteStatus] = useState("");
  const scriptIndex = useRef(0);
  const messageId = useRef(0);
  const scrollRef = useRef<HTMLDivElement>(null);
  const lastMessageId = messages.at(-1)?.id;

  useEffect(() => {
    if (state !== "active") return;
    const timer = window.setInterval(() => setElapsed((current) => current + 1), 1_000);
    return () => window.clearInterval(timer);
  }, [state]);

  useEffect(() => {
    if (state !== "connecting") return;
    const timer = window.setTimeout(() => setState("active"), 2_500);
    return () => window.clearTimeout(timer);
  }, [state]);

  useEffect(() => {
    if (state !== "active") return;
    const timer = window.setInterval(() => {
      const next = PREVIEW_SCRIPT[scriptIndex.current % PREVIEW_SCRIPT.length];
      scriptIndex.current += 1;
      messageId.current += 1;
      setMessages((current) => [...current, { ...next, id: `preview-${messageId.current}` }]);
    }, 5_000);
    return () => window.clearInterval(timer);
  }, [state]);

  useEffect(() => {
    if (!lastMessageId) return;
    const reduceMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
    scrollRef.current?.scrollTo?.({
      top: scrollRef.current.scrollHeight,
      behavior: reduceMotion ? "auto" : "smooth",
    });
  }, [lastMessageId]);

  const resetPreview = () => {
    scriptIndex.current = 0;
    messageId.current = 0;
    setState("active");
    setElapsed(INITIAL_ELAPSED);
    setMessages(INITIAL_MESSAGES);
    setNotes("");
    setNoteStatus("");
  };

  return (
    <div className="space-y-5">
      <PageIntro
        action={
          <div className="flex flex-wrap gap-2">
            <Button className="min-h-11" onClick={resetPreview} size="sm" variant="outline">
              <RefreshCw aria-hidden data-icon="inline-start" />
              Restart preview
            </Button>
            <Button
              className="min-h-11"
              disabled={state === "completed" || state === "failed"}
              onClick={() => setState("completed")}
              size="sm"
              variant="destructive"
            >
              <PhoneOff aria-hidden data-icon="inline-start" />
              End preview
            </Button>
          </div>
        }
        description="Explore how an active conversation could look while your receptionist handles it."
        eyebrow={
          <span className="flex items-center gap-2">
            Call workspace
            <CapabilityBadge status="preview" />
          </span>
        }
        title="Live call"
      />

      <aside className="rounded-lg border border-primary/20 bg-primary-soft px-4 py-3 text-sm" role="note">
        <span className="font-semibold text-accent-foreground">Preview only.</span>{" "}
        <span className="text-text-secondary">
          Nothing here places, answers, or ends a real call. All controls and notes stay in this browser session.
        </span>
      </aside>

      <section aria-label="Preview call overview" className="surface-card p-5">
        <div className="grid gap-4 sm:flex sm:items-center sm:justify-between">
          <div className="flex min-w-0 items-center gap-3">
            <span className="grid size-11 shrink-0 place-items-center rounded-full bg-primary-soft font-semibold text-accent-foreground text-sm">
              SB
            </span>
            <div className="min-w-0">
              <p className="truncate font-semibold text-base">Sophie Bernard</p>
              <p className="truncate text-muted-foreground text-sm">+33 6 12 34 56 78</p>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <span className="rounded-full bg-primary-soft px-2.5 py-1 font-medium text-accent-foreground text-xs">
              {STATE_LABEL[state]}
            </span>
            {state === "active" ? (
              <span className="inline-flex items-center gap-1.5 rounded-full bg-destructive/10 px-2.5 py-1 font-medium text-destructive text-xs">
                <CircleDot aria-hidden className="size-3 animate-pulse motion-reduce:animate-none" />
                Recording preview
              </span>
            ) : null}
            <span aria-live="polite" className="font-mono text-muted-foreground text-sm">
              {formatElapsed(elapsed)}
            </span>
          </div>
        </div>
      </section>

      <div className="grid gap-5 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <section aria-label="Live transcript" className="surface-card flex flex-col p-5">
          <div className="grid gap-3 sm:flex sm:items-center sm:justify-between">
            <h2 className="font-semibold text-sm">Live transcript</h2>
            <fieldset className="flex flex-wrap gap-1">
              <legend className="sr-only">Preview state controls</legend>
              {PREVIEW_STATES.map((option) => (
                <button
                  aria-pressed={state === option}
                  className={cn(
                    "min-h-11 rounded-full px-3 py-1 font-medium text-xs capitalize transition-colors",
                    state === option
                      ? "bg-primary text-primary-foreground"
                      : "bg-muted text-muted-foreground hover:bg-accent",
                  )}
                  key={option}
                  onClick={() => setState(option)}
                  type="button"
                >
                  {STATE_LABEL[option]}
                </button>
              ))}
            </fieldset>
          </div>

          {state === "connecting" ? (
            <p className="mt-6 text-center text-muted-foreground text-sm">Connecting to +33 6 12 34 56 78…</p>
          ) : null}
          {state === "failed" ? (
            <p className="mt-6 rounded-lg bg-destructive/10 p-4 text-center text-destructive text-sm">
              Preview failed — the simulated audio stream was lost.
            </p>
          ) : null}

          <div aria-live="polite" className="mt-4 max-h-[28rem] min-h-72 overflow-y-auto pr-1" ref={scrollRef}>
            <ol className="space-y-4">
              {messages.map((message) => (
                <PreviewTranscriptMessage key={message.id} message={message} />
              ))}
            </ol>
          </div>

          {state === "completed" ? (
            <div className="mt-4 rounded-lg bg-muted/60 p-4 text-sm">
              <p className="font-medium">Preview completed</p>
              <p className="mt-1 text-muted-foreground">
                No call history entry or recording was created. Restart to explore the flow again.
              </p>
            </div>
          ) : null}
        </section>

        <div className="space-y-5">
          <section aria-label="Caller information" className="surface-card p-5">
            <h2 className="flex items-center gap-2 font-semibold text-sm">
              <User2 aria-hidden className="size-4 text-muted-foreground" />
              Caller information
            </h2>
            <dl className="mt-4 space-y-3 text-sm">
              <div className="flex justify-between gap-3">
                <dt className="text-muted-foreground">Number</dt>
                <dd className="text-right font-medium">+33 6 12 34 56 78</dd>
              </div>
              <div className="flex justify-between gap-3">
                <dt className="text-muted-foreground">Name</dt>
                <dd className="text-right font-medium">Sophie Bernard</dd>
              </div>
              <div className="flex justify-between gap-3">
                <dt className="text-muted-foreground">Topic</dt>
                <dd className="text-right font-medium">Request a showroom appointment</dd>
              </div>
              <div className="flex justify-between gap-3">
                <dt className="text-muted-foreground">Answered by</dt>
                <dd className="font-medium">Camille</dd>
              </div>
              <div className="flex items-center justify-between gap-3">
                <dt className="text-muted-foreground">Recording</dt>
                <dd className="inline-flex items-center gap-1.5 font-medium">
                  <Mic aria-hidden className="size-3.5" />
                  {state === "active" ? "In progress" : "Stopped"}
                </dd>
              </div>
            </dl>
          </section>

          <section aria-label="Preview notes" className="surface-card p-5">
            <h2 className="flex items-center gap-2 font-semibold text-sm">
              <NotebookPen aria-hidden className="size-4 text-muted-foreground" />
              Preview notes
            </h2>
            <Textarea
              aria-label="Preview call notes"
              autoComplete="off"
              className="mt-3 min-h-32 resize-none"
              name="preview-call-notes"
              onChange={(event) => {
                setNotes(event.target.value);
                setNoteStatus("");
              }}
              placeholder="Jot down a follow-up for this preview…"
              value={notes}
            />
            <Button
              className="mt-3 min-h-11 w-full"
              disabled={!notes.trim()}
              onClick={() => setNoteStatus("Saved in this preview only")}
              size="sm"
            >
              Save preview note
            </Button>
            {noteStatus ? (
              <p aria-label="Preview note status" className="mt-3 text-center text-success text-xs" role="status">
                {noteStatus}
              </p>
            ) : null}
          </section>
        </div>
      </div>
    </div>
  );
}
