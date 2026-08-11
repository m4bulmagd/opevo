"use client";

import { useEffect } from "react";

import { useRouter } from "next/navigation";

import type { ActivationStage } from "@/lib/types/activation";

const REFRESH_INTERVAL_MS = 3_000;
const REFRESHING_STAGES = new Set<ActivationStage>(["provisioning", "verification_window_open", "activating"]);

export function StageRefresh({ stage }: { stage: ActivationStage }) {
  const router = useRouter();
  const shouldRefresh = REFRESHING_STAGES.has(stage);

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

    const start = (refreshImmediately: boolean) => {
      stop();
      if (document.visibilityState !== "visible") return;
      if (refreshImmediately) router.refresh();
      timer = setInterval(() => router.refresh(), REFRESH_INTERVAL_MS);
    };

    const handleVisibilityChange = () => {
      if (document.visibilityState === "visible") {
        start(true);
      } else {
        stop();
      }
    };

    start(false);
    document.addEventListener("visibilitychange", handleVisibilityChange);

    return () => {
      stop();
      document.removeEventListener("visibilitychange", handleVisibilityChange);
    };
  }, [router, shouldRefresh]);

  return null;
}
