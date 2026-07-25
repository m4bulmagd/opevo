/**
 * Applies the validated theme cookie before hydration so color mode never
 * depends on legacy visual-preference keys or a post-paint client effect.
 */
import { PREFERENCE_DEFAULTS } from "@/lib/preferences/preferences-config";
import { THEME_MODE_VALUES } from "@/lib/preferences/theme";

export function ThemeBootScript() {
  const defaultMode = JSON.stringify(PREFERENCE_DEFAULTS.theme_mode);
  const validModes = JSON.stringify(THEME_MODE_VALUES);
  const code = `
    (function () {
      try {
        var root = document.documentElement;
        var defaultMode = ${defaultMode};
        var validModes = ${validModes};
        var match = document.cookie.split("; ").find(function (cookie) {
          return cookie.startsWith("theme_mode=");
        });
        var cookieMode = match ? decodeURIComponent(match.slice("theme_mode=".length)) : null;
        var mode = validModes.includes(cookieMode) ? cookieMode : defaultMode;
        var prefersDark =
          mode === "system" &&
          window.matchMedia &&
          window.matchMedia("(prefers-color-scheme: dark)").matches;
        var resolvedMode = mode === "system" ? (prefersDark ? "dark" : "light") : mode;

        root.classList.toggle("dark", resolvedMode === "dark");
        root.setAttribute("data-theme-mode", mode);
        root.style.colorScheme = resolvedMode;
      } catch (error) {
        console.warn("ThemeBootScript error:", error);
      }
    })();
  `;

  /* biome-ignore lint/security/noDangerouslySetInnerHtml: required for the pre-hydration theme boot script. */
  return <script dangerouslySetInnerHTML={{ __html: code }} />;
}
