"use client";

import { startTransition, useCallback, useEffect, useRef, useState } from "react";

import type { ActivationActionResult, BusinessProfile, BusinessProfileDraft } from "@/lib/types/activation";

import { saveBusinessProfileAction } from "../../actions";

export type AutosaveStatus = "unsaved" | "saving" | "saved" | "error";

type UseProfileAutosaveOptions = {
  draft: BusinessProfileDraft;
  dirty: boolean;
  valid: boolean;
};

type ProfileSaveResult = ActivationActionResult<BusinessProfile>;

type PendingSave = {
  draft: BusinessProfileDraft;
  key: string;
};

type SaveOutcome = {
  key: string;
  result: ProfileSaveResult;
};

const SAVE_TRANSPORT_ERROR: ProfileSaveResult = {
  status: "error",
  code: "request_failed",
  message: "We couldn't save your profile. Check your connection and try again.",
};

export function useProfileAutosave({ draft, dirty, valid }: UseProfileAutosaveOptions) {
  const [status, setStatus] = useState<AutosaveStatus>("saved");
  const [message, setMessage] = useState<string | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const mountedRef = useRef(true);
  const latestDraftRef = useRef(draft);
  const latestKey = JSON.stringify(draft);
  const latestKeyRef = useRef(latestKey);
  const lastSavedKeyRef = useRef(latestKey);
  const inFlightKeyRef = useRef<string | null>(null);
  const queuedSaveRef = useRef<PendingSave | null>(null);
  const drainPromiseRef = useRef<Promise<SaveOutcome> | null>(null);
  const lastOutcomeRef = useRef<SaveOutcome | null>(null);
  const launchDrainRef = useRef<(() => Promise<SaveOutcome>) | null>(null);

  latestDraftRef.current = draft;
  latestKeyRef.current = latestKey;

  const clearTimer = useCallback(() => {
    if (!timerRef.current) return;
    clearTimeout(timerRef.current);
    timerRef.current = null;
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      clearTimer();
    };
  }, [clearTimer]);

  const performSave = useCallback(async (nextDraft: BusinessProfileDraft): Promise<ProfileSaveResult> => {
    return new Promise<ProfileSaveResult>((resolve) => {
      startTransition(() => {
        void saveBusinessProfileAction(nextDraft).then(resolve, () => resolve(SAVE_TRANSPORT_ERROR));
      });
    });
  }, []);

  const drainQueue = useCallback(
    async (firstSave: PendingSave): Promise<SaveOutcome> => {
      let request = firstSave;

      while (true) {
        inFlightKeyRef.current = request.key;
        if (mountedRef.current) {
          setStatus("saving");
          setMessage(null);
        }

        const result = await performSave(request.draft);
        const outcome = { key: request.key, result };
        lastOutcomeRef.current = outcome;
        inFlightKeyRef.current = null;

        if (result.status === "success") {
          lastSavedKeyRef.current = request.key;
        }

        const nextSave = queuedSaveRef.current;
        queuedSaveRef.current = null;
        if (nextSave) {
          request = nextSave;
          continue;
        }

        if (mountedRef.current) {
          if (result.status === "error" && request.key === latestKeyRef.current) {
            setStatus("error");
            setMessage(result.message);
          } else if (lastSavedKeyRef.current === latestKeyRef.current) {
            setStatus("saved");
            setMessage(result.status === "success" ? result.message : null);
          } else {
            setStatus("unsaved");
            setMessage(null);
          }
        }

        return outcome;
      }
    },
    [performSave],
  );

  const launchDrain = useCallback((): Promise<SaveOutcome> => {
    if (drainPromiseRef.current) return drainPromiseRef.current;

    const firstSave = queuedSaveRef.current;
    if (!firstSave) {
      throw new Error("Cannot start the profile save queue without a draft.");
    }
    queuedSaveRef.current = null;

    const drain = drainQueue(firstSave);
    drainPromiseRef.current = drain;
    const finishDrain = () => {
      if (drainPromiseRef.current === drain) {
        drainPromiseRef.current = null;
      }
      if (queuedSaveRef.current) {
        void launchDrainRef.current?.();
      }
    };
    void drain.then(finishDrain, finishDrain);
    return drain;
  }, [drainQueue]);

  launchDrainRef.current = launchDrain;

  const enqueueSave = useCallback(
    (nextDraft: BusinessProfileDraft): Promise<SaveOutcome> => {
      const requestKey = JSON.stringify(nextDraft);

      if (inFlightKeyRef.current === requestKey) {
        queuedSaveRef.current = null;
      } else {
        queuedSaveRef.current = { draft: nextDraft, key: requestKey };
      }

      if (mountedRef.current) {
        setStatus("saving");
        setMessage(null);
      }
      return launchDrain();
    },
    [launchDrain],
  );

  useEffect(() => {
    clearTimer();

    if (queuedSaveRef.current) {
      if (valid) {
        void enqueueSave(latestDraftRef.current);
      } else {
        queuedSaveRef.current = null;
        setStatus("unsaved");
        setMessage(null);
      }
      return;
    }

    const conflictingSave = inFlightKeyRef.current !== null && inFlightKeyRef.current !== latestKey;
    if (latestKey === lastSavedKeyRef.current && !conflictingSave) {
      setStatus("saved");
      setMessage(null);
      return;
    }

    setStatus("unsaved");
    setMessage(null);
    if (!valid || (!dirty && !conflictingSave)) return;

    timerRef.current = setTimeout(() => {
      timerRef.current = null;
      void enqueueSave(latestDraftRef.current);
    }, 700);

    return clearTimer;
  }, [clearTimer, dirty, enqueueSave, latestKey, valid]);

  const flush = useCallback(async (): Promise<ProfileSaveResult> => {
    while (true) {
      clearTimer();
      const requestDraft = latestDraftRef.current;
      const requestKey = latestKeyRef.current;
      await enqueueSave(requestDraft);

      if (requestKey !== latestKeyRef.current) continue;

      const outcome = lastOutcomeRef.current;
      if (outcome?.key !== requestKey) continue;
      if (outcome.result.status === "error") return outcome.result;
      if (lastSavedKeyRef.current === requestKey) return outcome.result;
    }
  }, [clearTimer, enqueueSave]);

  return { status, message, flush };
}
