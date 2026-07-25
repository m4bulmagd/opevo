import type { ThemeMode } from "./theme";

export type PreferenceValueMap = {
  theme_mode: ThemeMode;
};

export type PreferenceKey = keyof PreferenceValueMap;

export const PREFERENCE_DEFAULTS: PreferenceValueMap = {
  theme_mode: "light",
};

export const PREFERENCE_PERSISTENCE = {
  theme_mode: "client-cookie",
} as const;
