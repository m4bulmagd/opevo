"use client";

import { setClientCookie } from "../cookie.client";
import type { PreferenceKey } from "./preferences-config";
import { isThemeMode, type ThemeMode } from "./theme";

export function persistPreference(key: PreferenceKey, value: ThemeMode) {
  if (!isThemeMode(value)) return;
  setClientCookie(key, value);
}
