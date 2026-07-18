"use client";

import { useEffect } from "react";

import { useRouter } from "next/navigation";

import type { ActivationStage } from "@/lib/types/activation";

const REFRESH_INTERVAL_MS = 3_000;

export function StageRefresh({ stage }: { stage: ActivationStage }) {
  const router = useRouter();
  const shouldRefresh = stage === "provisioning" || stage === "activating";

  useEffect(() => {
    if (!shouldRefresh) {
      return;
    }

    let timer: ReturnType<typeof setInterval> | undefined;

    const stop = () => {
      if (timer !== undefined) {
        clearInterval(timer);
        timer = undefined;
      }
    };

    const start = () => {
      stop();
      if (document.visibilityState === "visible") {
        timer = setInterval(() => router.refresh(), REFRESH_INTERVAL_MS);
      }
    };

    const handleVisibilityChange = () => {
      if (document.visibilityState === "visible") {
        start();
      } else {
        stop();
      }
    };

    start();
    document.addEventListener("visibilitychange", handleVisibilityChange);

    return () => {
      stop();
      document.removeEventListener("visibilitychange", handleVisibilityChange);
    };
  }, [router, shouldRefresh]);

  return null;
}
