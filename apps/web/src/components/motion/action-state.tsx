"use client";

// Adapted from https://beui.dev/components/motion/action-swap
// The consumer remains the semantic/styling owner of its button or control.

import type { ReactNode } from "react";

import { AnimatePresence, motion, useReducedMotion, type Variants } from "motion/react";

import { EASE_OUT } from "@/lib/motion/tokens";

export type ActionPhase = "idle" | "pending" | "success" | "error";

export type ActionStateProps = {
  phase: ActionPhase;
  idle: ReactNode;
  pending: ReactNode;
  success: ReactNode;
  error: ReactNode;
};

const ACTION_VARIANTS: Variants = {
  initial: { opacity: 0, y: 3 },
  animate: {
    opacity: 1,
    y: 0,
    transition: { duration: 0.16, ease: EASE_OUT },
  },
  exit: {
    opacity: 0,
    y: -2,
    transition: { duration: 0.12, ease: EASE_OUT },
  },
};

const REDUCED_ACTION_VARIANTS: Variants = {
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

export function ActionState({ phase, idle, pending, success, error }: ActionStateProps) {
  const reduceMotion = useReducedMotion();
  const content = { idle, pending, success, error }[phase];

  return (
    <span aria-atomic="true" aria-live="polite" data-phase={phase}>
      <AnimatePresence initial={false}>
        <motion.span
          key={phase}
          animate="animate"
          className="inline-flex items-center justify-center"
          exit="exit"
          initial="initial"
          variants={reduceMotion ? REDUCED_ACTION_VARIANTS : ACTION_VARIANTS}
        >
          {content}
        </motion.span>
      </AnimatePresence>
    </span>
  );
}
