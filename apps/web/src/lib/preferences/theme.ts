export const THEME_MODE_OPTIONS = [
  { label: "Light", value: "light" },
  { label: "Dark", value: "dark" },
  { label: "System", value: "system" },
] as const;

export const THEME_MODE_VALUES = THEME_MODE_OPTIONS.map((option) => option.value);

export type ThemeMode = (typeof THEME_MODE_VALUES)[number];
export type ResolvedThemeMode = Exclude<ThemeMode, "system">;

export function isThemeMode(value: unknown): value is ThemeMode {
  return typeof value === "string" && THEME_MODE_VALUES.includes(value as ThemeMode);
}
