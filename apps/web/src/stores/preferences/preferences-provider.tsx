"use client";

import { createContext, useContext, useEffect, useRef, useState } from "react";

import { type StoreApi, useStore } from "zustand";

import { isThemeMode } from "@/lib/preferences/theme";
import { applyThemeMode, subscribeToSystemTheme } from "@/lib/preferences/theme-utils";

import { createPreferencesStore, type PreferencesState } from "./preferences-store";

const PreferencesStoreContext = createContext<StoreApi<PreferencesState> | null>(null);

function readDomState() {
  const root = document.documentElement;
  const rawThemeMode = root.getAttribute("data-theme-mode");

  return {
    themeMode: isThemeMode(rawThemeMode) ? rawThemeMode : undefined,
    resolvedThemeMode: root.classList.contains("dark") ? ("dark" as const) : ("light" as const),
  };
}

export function PreferencesStoreProvider({
  children,
  themeMode,
}: {
  children?: React.ReactNode;
  themeMode: PreferencesState["themeMode"];
}) {
  const [store] = useState<StoreApi<PreferencesState>>(() =>
    createPreferencesStore({
      themeMode,
      resolvedThemeMode: themeMode === "dark" ? "dark" : "light",
    }),
  );
  const domSnapshotRef = useRef<ReturnType<typeof readDomState> | null>(null);

  useEffect(() => {
    const domState = readDomState();
    domSnapshotRef.current = domState;

    store.setState((previous) => ({
      ...previous,
      themeMode: domState.themeMode ?? previous.themeMode,
      resolvedThemeMode: domState.resolvedThemeMode,
      isSynced: true,
    }));
  }, [store]);

  useEffect(() => {
    let unsubscribeMedia: (() => void) | undefined;

    const applyFromMode = (mode: PreferencesState["themeMode"]) => {
      unsubscribeMedia?.();
      const resolvedThemeMode = applyThemeMode(mode);
      store.setState({ resolvedThemeMode });

      if (mode === "system") {
        unsubscribeMedia = subscribeToSystemTheme(() => {
          store.setState({ resolvedThemeMode: applyThemeMode("system") });
        });
      }
    };

    applyFromMode(domSnapshotRef.current?.themeMode ?? store.getState().themeMode);

    const unsubscribeStore = store.subscribe((state, previous) => {
      if (state.themeMode !== previous.themeMode) applyFromMode(state.themeMode);
    });

    return () => {
      unsubscribeMedia?.();
      unsubscribeStore();
    };
  }, [store]);

  return <PreferencesStoreContext.Provider value={store}>{children}</PreferencesStoreContext.Provider>;
}

export function usePreferencesStore<T>(selector: (state: PreferencesState) => T): T {
  const store = useContext(PreferencesStoreContext);
  if (!store) throw new Error("Missing PreferencesStoreProvider");
  return useStore(store, selector);
}
