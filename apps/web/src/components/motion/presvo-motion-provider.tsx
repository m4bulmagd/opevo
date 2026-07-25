"use client";

import { MotionConfig } from "motion/react";

export function PresvoMotionProvider({ children }: { children: React.ReactNode }) {
  return <MotionConfig reducedMotion="user">{children}</MotionConfig>;
}
