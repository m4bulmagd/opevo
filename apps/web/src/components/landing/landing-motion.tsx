"use client";

import type { ReactNode } from "react";

import type { HTMLMotionProps } from "motion/react";
import { motion, useReducedMotion } from "motion/react";

import { cn } from "@/lib/utils";

const LANDING_EASE = [0.22, 1, 0.36, 1] as const;

type MotionDivProps = HTMLMotionProps<"div"> & {
  children: ReactNode;
  delay?: number;
};

export function LandingMotionGroup({ children, className, delay = 0, ...props }: MotionDivProps) {
  const reduceMotion = useReducedMotion();

  return (
    <motion.div
      data-motion="stagger"
      initial={reduceMotion ? false : "hidden"}
      animate={reduceMotion ? undefined : "visible"}
      variants={{
        hidden: {},
        visible: {
          transition: {
            staggerChildren: 0.1,
            delayChildren: delay,
          },
        },
      }}
      className={cn(className)}
      {...props}
    >
      {children}
    </motion.div>
  );
}

export function LandingMotionItem({ children, className, ...props }: MotionDivProps) {
  const reduceMotion = useReducedMotion();

  return (
    <motion.div
      data-motion="fade-up"
      variants={
        reduceMotion
          ? undefined
          : {
              hidden: { opacity: 0, y: 18 },
              visible: {
                opacity: 1,
                y: 0,
                transition: {
                  duration: 0.68,
                  ease: LANDING_EASE,
                },
              },
            }
      }
      className={cn(className)}
      {...props}
    >
      {children}
    </motion.div>
  );
}

export function LandingMotionFade({ children, className, delay = 0, ...props }: MotionDivProps) {
  const reduceMotion = useReducedMotion();

  return (
    <motion.div
      data-motion="fade-up"
      initial={reduceMotion ? false : { opacity: 0, y: 20 }}
      animate={reduceMotion ? undefined : { opacity: 1, y: 0 }}
      transition={
        reduceMotion
          ? undefined
          : {
              duration: 0.72,
              delay,
              ease: LANDING_EASE,
            }
      }
      className={cn(className)}
      {...props}
    >
      {children}
    </motion.div>
  );
}

export function LandingMotionCard({ children, className, delay = 0, ...props }: MotionDivProps) {
  const reduceMotion = useReducedMotion();

  return (
    <motion.div
      data-motion="fade-up"
      initial={reduceMotion ? false : { opacity: 0, y: 18 }}
      animate={reduceMotion ? undefined : { opacity: 1, y: 0 }}
      whileHover={
        reduceMotion
          ? undefined
          : {
              y: -6,
              scale: 1.01,
              transition: {
                duration: 0.24,
                ease: LANDING_EASE,
              },
            }
      }
      whileTap={reduceMotion ? undefined : { scale: 0.995 }}
      transition={
        reduceMotion
          ? undefined
          : {
              duration: 0.68,
              delay,
              ease: LANDING_EASE,
            }
      }
      className={cn("will-change-transform", className)}
      {...props}
    >
      {children}
    </motion.div>
  );
}

export function LandingAmbientGlow({ className }: { className?: string }) {
  const reduceMotion = useReducedMotion();

  return (
    <motion.div
      aria-hidden
      data-motion="ambient"
      className={className}
      animate={
        reduceMotion
          ? undefined
          : {
              x: [0, 16, 0],
              scale: [1, 1.04, 1],
              opacity: [0.82, 0.94, 0.82],
            }
      }
      transition={
        reduceMotion
          ? undefined
          : {
              duration: 9,
              repeat: Number.POSITIVE_INFINITY,
              ease: "easeInOut",
            }
      }
    />
  );
}
