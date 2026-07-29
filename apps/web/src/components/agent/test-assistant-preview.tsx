"use client";

import { useEffect, useState } from "react";

import { Mic, MicOff, PhoneOff, RefreshCw } from "lucide-react";

import { CapabilityBadge } from "@/components/product/capability-badge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetFooter, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { formatDuration } from "@/lib/formatters";
import { cn } from "@/lib/utils";

type PreviewPhase = "connecting" | "speaking" | "listening" | "ended";

type PreviewMessage = {
  id: string;
  role: "assistant" | "caller" | "system";
  text: string;
};

const INITIAL_MESSAGES: PreviewMessage[] = [
  {
    id: "preview-system",
    role: "system",
    text: "Simulation locale — aucun appel n’est placé et aucune minute n’est consommée.",
  },
];

function phaseLabel(phase: PreviewPhase, agentName: string): string {
  if (phase === "connecting") return "Connecting";
  if (phase === "speaking") return `${agentName} is speaking`;
  if (phase === "listening") return "Listening to you";
  return "Preview ended";
}

export function TestAssistantPreview({
  agentName,
  onOpenChange,
  open,
}: {
  agentName: string;
  onOpenChange: (open: boolean) => void;
  open: boolean;
}) {
  const [phase, setPhase] = useState<PreviewPhase>("connecting");
  const [elapsed, setElapsed] = useState(0);
  const [muted, setMuted] = useState(false);
  const [messages, setMessages] = useState<PreviewMessage[]>(INITIAL_MESSAGES);

  const restartPreview = () => {
    setPhase("connecting");
    setElapsed(0);
    setMuted(false);
    setMessages(INITIAL_MESSAGES);
  };

  useEffect(() => {
    if (open) {
      setPhase("connecting");
      setElapsed(0);
      setMuted(false);
      setMessages(INITIAL_MESSAGES);
    }
  }, [open]);

  useEffect(() => {
    if (!open || phase === "ended") {
      return;
    }

    const timer = window.setInterval(() => setElapsed((value) => value + 1), 1000);
    return () => window.clearInterval(timer);
  }, [open, phase]);

  useEffect(() => {
    if (!open || phase === "ended") {
      return;
    }

    const delay = phase === "connecting" ? 800 : phase === "speaking" ? 1600 : 1500;
    const timer = window.setTimeout(() => {
      if (phase === "connecting") {
        setMessages((current) => [
          ...current,
          {
            id: "preview-greeting",
            role: "assistant",
            text: `Bonjour, vous êtes bien chez Atelier Marceau. Je suis ${agentName}. Comment puis-je vous aider ?`,
          },
        ]);
        setPhase("speaking");
        return;
      }

      if (phase === "speaking") {
        setPhase("listening");
        return;
      }

      setMessages((current) => [
        ...current,
        {
          id: `preview-caller-${current.length}`,
          role: "caller",
          text: "Bonjour, je voudrais prendre rendez-vous jeudi après-midi.",
        },
        {
          id: `preview-assistant-${current.length}`,
          role: "assistant",
          text: "Bien sûr. Je peux vous proposer quinze heures au showroom de Paris.",
        },
      ]);
      setPhase("speaking");
    }, delay);

    return () => window.clearTimeout(timer);
  }, [agentName, open, phase]);

  return (
    <Sheet onOpenChange={onOpenChange} open={open}>
      <SheetContent className="w-full gap-0 p-0 sm:max-w-md" side="right">
        <SheetHeader className="border-border border-b p-5 pr-14">
          <div className="flex items-center gap-2">
            <SheetTitle className="truncate text-base">Test {agentName}</SheetTitle>
            <CapabilityBadge status="preview" />
          </div>
          <SheetDescription>
            Local simulation using the Preview settings. No call is placed and no minutes are used.
          </SheetDescription>
        </SheetHeader>

        <div className="flex flex-col items-center gap-4 border-border border-b bg-muted/40 px-5 py-6">
          <div
            aria-hidden
            className={cn(
              "grid size-20 place-items-center rounded-full bg-primary font-semibold text-primary-foreground text-xl",
              phase !== "ended" && "motion-safe:animate-pulse",
            )}
          >
            {agentName.slice(0, 2).toUpperCase()}
          </div>
          <Badge
            aria-label="Preview call status"
            className="min-h-7"
            role="status"
            variant={phase === "ended" ? "secondary" : "default"}
          >
            {phaseLabel(phase, agentName)}
          </Badge>
          <div aria-hidden className="flex h-6 items-end gap-1">
            {[10, 18, 12, 22, 15, 20, 9, 16].map((height) => (
              <span
                className={cn("w-1 rounded-full bg-primary/55", phase === "speaking" && "motion-safe:animate-pulse")}
                key={height}
                style={{ height }}
              />
            ))}
          </div>
          <p className="font-mono text-sm text-text-secondary">{formatDuration(elapsed)}</p>
        </div>

        <div
          aria-label="Preview test transcript"
          aria-live="polite"
          className="flex-1 space-y-3 overflow-y-auto p-5"
          role="log"
        >
          {messages.map((message) => (
            <div
              className={cn(
                "max-w-[86%] rounded-xl px-3.5 py-2.5 text-sm leading-relaxed",
                message.role === "assistant" && "bg-primary-soft text-accent-foreground",
                message.role === "caller" && "ml-auto bg-muted text-text-primary",
                message.role === "system" &&
                  "mx-auto max-w-full bg-transparent text-center text-text-secondary text-xs",
              )}
              key={message.id}
            >
              {message.text}
            </div>
          ))}
        </div>

        <SheetFooter className="grid grid-cols-1 gap-2 border-border border-t p-4 sm:grid-cols-3">
          <Button
            aria-pressed={muted}
            className="min-h-11"
            onClick={() => setMuted((value) => !value)}
            variant={muted ? "secondary" : "outline"}
          >
            {muted ? <MicOff aria-hidden data-icon="inline-start" /> : <Mic aria-hidden data-icon="inline-start" />}
            {muted ? "Unmute" : "Mute"}
          </Button>
          <Button className="min-h-11" onClick={restartPreview} variant="outline">
            <RefreshCw aria-hidden data-icon="inline-start" />
            Restart preview
          </Button>
          <Button
            className="min-h-11"
            disabled={phase === "ended"}
            onClick={() => setPhase("ended")}
            variant="destructive"
          >
            <PhoneOff aria-hidden data-icon="inline-start" />
            End preview
          </Button>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  );
}
