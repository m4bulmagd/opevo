import { createStore } from "zustand/vanilla";

import { PREFERENCE_DEFAULTS } from "@/lib/preferences/preferences-config";
import type { ResolvedThemeMode, ThemeMode } from "@/lib/preferences/theme";

export type PreferencesState = {
  themeMode: ThemeMode;
  resolvedThemeMode: ResolvedThemeMode;
  setThemeMode: (mode: ThemeMode) => void;
  setResolvedThemeMode: (mode: ResolvedThemeMode) => void;
  isSynced: boolean;
  setIsSynced: (value: boolean) => void;
};

type PreferencesInitialState = Pick<PreferencesState, "themeMode" | "resolvedThemeMode" | "isSynced">;

export const createPreferencesStore = (initialState?: Partial<PreferencesInitialState>) =>
  createStore<PreferencesState>()((set) => ({
    themeMode: initialState?.themeMode ?? PREFERENCE_DEFAULTS.theme_mode,
    resolvedThemeMode: initialState?.resolvedThemeMode ?? "light",
    setThemeMode: (mode) => set({ themeMode: mode }),
    setResolvedThemeMode: (mode) => set({ resolvedThemeMode: mode }),
    isSynced: initialState?.isSynced ?? false,
    setIsSynced: (value) => set({ isSynced: value }),
  }));
