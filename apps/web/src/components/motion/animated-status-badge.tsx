"use client";

// Adapted from https://beui.dev/components/motion/animated-badge
// Continuous pulse/spin behavior was intentionally removed for Opevo.

import type { ReactNode } from "react";

import { AnimatePresence, motion, type Variants } from "motion/react";

import { EASE_OUT } from "@/lib/motion/tokens";
import { useHydrationSafeReducedMotion } from "@/lib/motion/use-hydration-safe-reduced-motion";
import { cn } from "@/lib/utils";

export type StatusTone = "neutral" | "live" | "ready" | "processing" | "paused" | "warning" | "attention" | "inactive";

export type AnimatedStatusBadgeProps = {
  tone: StatusTone;
  label: string;
  icon?: ReactNode;
  className?: string;
};

const TONE_CLASS: Record<StatusTone, string> = {
  neutral: "border-border bg-surface-subtle text-text-secondary",
  live: "border-success/30 bg-success-subtle text-success",
  ready: "border-primary/30 bg-primary/10 text-primary",
  processing: "border-primary/30 bg-primary/10 text-primary",
  paused: "border-border bg-surface-subtle text-text-secondary",
  warning: "border-warning/30 bg-warning-subtle text-warning",
  attention: "border-warning/30 bg-warning-subtle text-warning",
  inactive: "border-border bg-muted text-muted-foreground",
};

const MOTION_VARIANTS: Variants = {
  initial: { opacity: 0, y: 4 },
  animate: {
    opacity: 1,
    y: 0,
    transition: { duration: 0.18, ease: EASE_OUT },
  },
  exit: {
    opacity: 0,
    y: -3,
    transition: { duration: 0.12, ease: EASE_OUT },
  },
};

const REDUCED_MOTION_VARIANTS: Variants = {
  initial: { opacity: 0 },
  animate: {
    opacity: 1,
    transition: { duration: 0.12, ease: EASE_OUT },
  },
  exit: {
    opacity: 0,
    transition: { duration: 0.08, ease: EASE_OUT },
  },
};

export function AnimatedStatusBadge({ tone, label, icon, className }: AnimatedStatusBadgeProps) {
  const reduceMotion = useHydrationSafeReducedMotion();
  const variants = reduceMotion ? REDUCED_MOTION_VARIANTS : MOTION_VARIANTS;

  return (
    <span
      className={cn(
        "inline-flex h-7 shrink-0 items-center overflow-hidden rounded-full border px-2.5 font-medium text-xs",
        TONE_CLASS[tone],
        className,
      )}
      data-tone={tone}
    >
      <AnimatePresence initial={false}>
        <motion.span
          key={`${tone}:${label}`}
          animate="animate"
          className="inline-flex items-center gap-1.5"
          exit="exit"
          initial="initial"
          variants={variants}
        >
          {icon ?? <span aria-hidden className="size-1.5 rounded-full bg-current" />}
          <span>{label}</span>
        </motion.span>
      </AnimatePresence>
    </span>
  );
}
