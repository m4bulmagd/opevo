"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import { useRouter } from "next/navigation";

type VerificationCountdownProps = {
  evaluatedAt: string;
  expiresAt: string;
};

function formatRemaining(remainingMs: number): string {
  const seconds = Math.max(0, Math.ceil(remainingMs / 1_000));
  const minutesPart = Math.floor(seconds / 60)
    .toString()
    .padStart(2, "0");
  const secondsPart = (seconds % 60).toString().padStart(2, "0");
  return `${minutesPart}:${secondsPart}`;
}

function calculateRemaining(expiryMs: number, serverOffsetMs: number): number {
  return Math.max(0, expiryMs - (Date.now() + serverOffsetMs));
}

export function VerificationCountdown({ evaluatedAt, expiresAt }: VerificationCountdownProps) {
  const { refresh } = useRouter();
  const serverOffsetMs = useMemo(() => Date.parse(evaluatedAt) - Date.now(), [evaluatedAt]);
  const expiryMs = Date.parse(expiresAt);
  const validTimes = Number.isFinite(serverOffsetMs) && Number.isFinite(expiryMs);
  const [remainingMs, setRemainingMs] = useState(() =>
    validTimes ? calculateRemaining(expiryMs, serverOffsetMs) : Number.NaN,
  );
  const refreshedAtZero = useRef(false);

  useEffect(() => {
    if (!validTimes) return;

    const update = (): boolean => {
      const nextRemaining = calculateRemaining(expiryMs, serverOffsetMs);
      setRemainingMs(nextRemaining);
      if (nextRemaining === 0 && !refreshedAtZero.current) {
        refreshedAtZero.current = true;
        refresh();
      }
      return nextRemaining === 0;
    };

    if (update()) return;
    const timer = setInterval(() => {
      if (update()) clearInterval(timer);
    }, 1_000);
    return () => clearInterval(timer);
  }, [expiryMs, refresh, serverOffsetMs, validTimes]);

  return (
    <span role="timer" aria-live="off" aria-label="Verification time remaining" className="font-semibold tabular-nums">
      {validTimes && Number.isFinite(remainingMs) ? formatRemaining(remainingMs) : "--:--"}
    </span>
  );
}
