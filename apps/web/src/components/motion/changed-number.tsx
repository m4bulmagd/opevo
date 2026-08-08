"use client";

// Adapted from https://beui.dev/components/motion/number
// Opevo renders the authoritative initial value and animates updates only.

import { useEffect, useRef, useState } from "react";

import { animate, useReducedMotion } from "motion/react";

import { EASE_OUT } from "@/lib/motion/tokens";
import { cn } from "@/lib/utils";

export type ChangedNumberProps = {
  value: number;
  duration?: number;
  format?: (value: number) => string;
  className?: string;
};

const formatNumber = (value: number) => Math.round(value).toLocaleString("fr-FR");

export function ChangedNumber({ value, duration = 0.24, format = formatNumber, className }: ChangedNumberProps) {
  const reduceMotion = useReducedMotion();
  const [displayValue, setDisplayValue] = useState(value);
  const previousValueRef = useRef(value);
  const hasMountedRef = useRef(false);
  const durationRef = useRef(duration);
  durationRef.current = duration;

  useEffect(() => {
    if (!hasMountedRef.current) {
      hasMountedRef.current = true;
      previousValueRef.current = value;
      return;
    }

    const previousValue = previousValueRef.current;

    if (reduceMotion) {
      previousValueRef.current = value;
      setDisplayValue(value);
      return;
    }

    if (previousValue === value) return;

    previousValueRef.current = value;
    const controls = animate(previousValue, value, {
      duration: durationRef.current,
      ease: EASE_OUT,
      onUpdate: setDisplayValue,
    });

    return () => controls.stop();
  }, [reduceMotion, value]);

  return <span className={cn("tabular-nums", className)}>{format(reduceMotion ? value : displayValue)}</span>;
}
