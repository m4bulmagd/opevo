"use client";

import { motion, useReducedMotion } from "motion/react";

import { EASE_DRAWER } from "@/lib/motion/tokens";

// Adapted from BeUI's bottom-sheet presentation:
// https://beui.dev/components/motion/bottom-sheet
// Vaul remains the sole owner of dialog semantics, portal, dismissal, focus,
// scroll locking, and restoration. This wrapper provides presentation only.
export function BottomSheet({ children }: { children: React.ReactNode }) {
  const shouldReduceMotion = useReducedMotion();

  return (
    <motion.div
      className="flex min-h-0 flex-col"
      data-motion={shouldReduceMotion ? "opacity-only" : "transform-opacity"}
      data-slot="bottom-sheet-motion"
      initial={shouldReduceMotion ? { opacity: 0 } : { opacity: 0, y: 24 }}
      animate={shouldReduceMotion ? { opacity: 1 } : { opacity: 1, y: 0 }}
      transition={{ duration: shouldReduceMotion ? 0.16 : 0.24, ease: EASE_DRAWER }}
    >
      {children}
    </motion.div>
  );
}
