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

export function useProfileAutosave({ draft, dirty, valid }: UseProfileAutosaveOptions) {
  const [status, setStatus] = useState<AutosaveStatus>("saved");
  const [message, setMessage] = useState<string | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const sequenceRef = useRef(0);
  const mountedRef = useRef(true);
  const latestDraftRef = useRef(draft);
  const latestKey = JSON.stringify(draft);
  const latestKeyRef = useRef(latestKey);
  const lastSavedKeyRef = useRef(JSON.stringify(draft));

  latestDraftRef.current = draft;
  latestKeyRef.current = latestKey;

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, []);

  const issueSave = useCallback(async (nextDraft: BusinessProfileDraft): Promise<ProfileSaveResult> => {
    const requestSequence = sequenceRef.current + 1;
    sequenceRef.current = requestSequence;
    const requestKey = JSON.stringify(nextDraft);
    setStatus("saving");
    setMessage(null);

    const result = await new Promise<ProfileSaveResult>((resolve) => {
      startTransition(() => {
        void saveBusinessProfileAction(nextDraft).then(resolve);
      });
    });

    if (mountedRef.current && requestSequence === sequenceRef.current && requestKey === latestKeyRef.current) {
      if (result.status === "success") {
        lastSavedKeyRef.current = requestKey;
        setStatus("saved");
        setMessage(result.message);
      } else {
        setStatus("error");
        setMessage(result.message);
      }
    }
    return result;
  }, []);

  useEffect(() => {
    if (!dirty || latestKey === lastSavedKeyRef.current) return;
    if (timerRef.current) clearTimeout(timerRef.current);
    setStatus("unsaved");
    setMessage(null);
    if (!valid) return;
    timerRef.current = setTimeout(() => {
      timerRef.current = null;
      void issueSave(latestDraftRef.current);
    }, 700);
    return () => {
      if (timerRef.current) {
        clearTimeout(timerRef.current);
        timerRef.current = null;
      }
    };
  }, [dirty, issueSave, latestKey, valid]);

  const flush = useCallback(async (): Promise<ProfileSaveResult> => {
    if (timerRef.current) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
    return issueSave(latestDraftRef.current);
  }, [issueSave]);

  return { status, message, flush };
}
