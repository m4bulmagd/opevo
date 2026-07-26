"use client";

import { useSyncExternalStore } from "react";

import { useReducedMotion } from "motion/react";

const subscribe = () => () => undefined;
const clientSnapshot = () => true;
const serverSnapshot = () => false;

export function useHydrationSafeReducedMotion(): boolean {
  const shouldReduceMotion = useReducedMotion();
  const hasHydrated = useSyncExternalStore(subscribe, clientSnapshot, serverSnapshot);

  return !hasHydrated || Boolean(shouldReduceMotion);
}
