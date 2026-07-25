import { createElement, Fragment, type ReactNode } from "react";

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ThemeSwitcher } from "@/app/(app)/dashboard/_components/sidebar/theme-switcher";
import { THEME_MODE_VALUES } from "@/lib/preferences/theme";
import { ThemeBootScript } from "@/scripts/theme-boot";
import { PreferencesStoreProvider, usePreferencesStore } from "@/stores/preferences/preferences-provider";

const LEGACY_ATTRIBUTES = [
  "data-theme-preset",
  "data-font",
  "data-content-layout",
  "data-navbar-style",
  "data-sidebar-variant",
  "data-sidebar-collapsible",
] as const;

const LEGACY_PREFERENCES = [
  ["theme_preset", "tangerine"],
  ["font", "roboto"],
  ["content_layout", "full-width"],
  ["navbar_style", "scroll"],
  ["sidebar_variant", "floating"],
  ["sidebar_collapsible", "offcanvas"],
] as const;

function clearCookie(name: string) {
  // biome-ignore lint/suspicious/noDocumentCookie: jsdom has no Cookie Store API.
  document.cookie = `${name}=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/`;
}

function installLocalStorage() {
  const values = new Map<string, string>();
  const storage: Storage = {
    get length() {
      return values.size;
    },
    clear: () => values.clear(),
    getItem: (key) => values.get(key) ?? null,
    key: (index) => Array.from(values.keys())[index] ?? null,
    removeItem: (key) => {
      values.delete(key);
    },
    setItem: (key, value) => {
      values.set(key, value);
    },
  };

  Object.defineProperty(window, "localStorage", {
    configurable: true,
    value: storage,
  });
}

function resetThemeEnvironment() {
  document.documentElement.classList.remove("dark", "disable-transitions");
  document.documentElement.removeAttribute("data-theme-mode");
  document.documentElement.style.colorScheme = "";

  for (const attribute of LEGACY_ATTRIBUTES) {
    document.documentElement.removeAttribute(attribute);
  }

  clearCookie("theme_mode");
  for (const [key] of LEGACY_PREFERENCES) {
    clearCookie(key);
  }
  window.localStorage.clear();
}

function installMatchMedia(initiallyDark: boolean) {
  let listener: ((event: MediaQueryListEvent) => void) | undefined;
  const mediaQuery = {
    matches: initiallyDark,
    media: "(prefers-color-scheme: dark)",
    onchange: null,
    addEventListener: vi.fn((_event: string, nextListener: (event: MediaQueryListEvent) => void) => {
      listener = nextListener;
    }),
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(),
  };

  vi.stubGlobal(
    "matchMedia",
    vi.fn(() => mediaQuery),
  );

  return {
    change(matches: boolean) {
      mediaQuery.matches = matches;
      listener?.({ matches } as MediaQueryListEvent);
    },
  };
}

function runThemeBootScript() {
  const { container } = render(createElement(ThemeBootScript));
  const source = container.querySelector("script")?.textContent;

  expect(source).toBeTruthy();
  new Function(source ?? "")();
}

function ThemeProbe() {
  const themeMode = usePreferencesStore((state) => state.themeMode);
  const resolvedThemeMode = usePreferencesStore((state) => state.resolvedThemeMode);
  const setThemeMode = usePreferencesStore((state) => state.setThemeMode);

  return createElement(
    Fragment,
    null,
    createElement("output", null, `${themeMode}:${resolvedThemeMode}`),
    createElement(
      "button",
      {
        type: "button",
        onClick: () => setThemeMode("system"),
      },
      "Follow system",
    ),
  );
}

function Provider({
  children,
  themeMode = "light",
}: {
  children?: ReactNode;
  themeMode?: "light" | "dark" | "system";
}) {
  return createElement(PreferencesStoreProvider, { themeMode }, children);
}

beforeEach(installLocalStorage);

afterEach(() => {
  resetThemeEnvironment();
  cleanup();
  vi.unstubAllGlobals();
});

describe("curated theme preferences", () => {
  it("accepts only light, dark, and system theme modes", () => {
    expect(THEME_MODE_VALUES).toEqual(["light", "dark", "system"]);

    installMatchMedia(false);
    // biome-ignore lint/suspicious/noDocumentCookie: jsdom has no Cookie Store API.
    document.cookie = "theme_mode=brutalist; path=/";

    runThemeBootScript();

    expect(document.documentElement).toHaveAttribute("data-theme-mode", "light");
    expect(document.documentElement).not.toHaveClass("dark");
    expect(document.documentElement.style.colorScheme).toBe("light");
  });

  it.each([
    ["light", false, false, "light"],
    ["dark", false, true, "dark"],
    ["system", true, true, "dark"],
  ] as const)("applies %s before paint", (mode, systemDark, expectDark, colorScheme) => {
    installMatchMedia(systemDark);
    // biome-ignore lint/suspicious/noDocumentCookie: jsdom has no Cookie Store API.
    document.cookie = `theme_mode=${mode}; path=/`;

    runThemeBootScript();

    expect(document.documentElement).toHaveAttribute("data-theme-mode", mode);
    expect(document.documentElement.classList.contains("dark")).toBe(expectDark);
    expect(document.documentElement.style.colorScheme).toBe(colorScheme);
  });

  it("ignores legacy preset, font, and layout values from cookies and local storage", () => {
    installMatchMedia(false);
    // biome-ignore lint/suspicious/noDocumentCookie: jsdom has no Cookie Store API.
    document.cookie = "theme_mode=dark; path=/";

    for (const [key, value] of LEGACY_PREFERENCES) {
      // biome-ignore lint/suspicious/noDocumentCookie: jsdom has no Cookie Store API.
      document.cookie = `${key}=${value}; path=/`;
      window.localStorage.setItem(key, value);
    }

    runThemeBootScript();

    expect(document.documentElement).toHaveAttribute("data-theme-mode", "dark");
    expect(document.documentElement).toHaveClass("dark");
    for (const attribute of LEGACY_ATTRIBUTES) {
      expect(document.documentElement).not.toHaveAttribute(attribute);
    }
  });

  it("changes only theme DOM state and persists only theme_mode", async () => {
    installMatchMedia(false);
    for (const [index, attribute] of LEGACY_ATTRIBUTES.entries()) {
      document.documentElement.setAttribute(attribute, LEGACY_PREFERENCES[index]?.[1] ?? "legacy");
    }

    render(createElement(Provider, null, createElement(ThemeSwitcher)));

    fireEvent.click(screen.getByRole("button", { name: /current theme: light/i }));

    await waitFor(() => expect(document.documentElement).toHaveClass("dark"));
    expect(document.documentElement).toHaveAttribute("data-theme-mode", "dark");
    expect(document.documentElement.style.colorScheme).toBe("dark");
    expect(document.cookie).toContain("theme_mode=dark");

    for (const [index, attribute] of LEGACY_ATTRIBUTES.entries()) {
      expect(document.documentElement).toHaveAttribute(attribute, LEGACY_PREFERENCES[index]?.[1] ?? "legacy");
    }
    for (const [key] of LEGACY_PREFERENCES) {
      expect(document.cookie).not.toContain(`${key}=`);
      expect(window.localStorage.getItem(key)).toBeNull();
    }
  });

  it("follows media-query changes while system mode is active", async () => {
    const media = installMatchMedia(true);

    render(createElement(Provider, { themeMode: "system" }, createElement(ThemeProbe)));

    await waitFor(() => {
      expect(screen.getByText("system:dark")).toBeInTheDocument();
      expect(document.documentElement).toHaveClass("dark");
    });

    act(() => media.change(false));

    await waitFor(() => {
      expect(screen.getByText("system:light")).toBeInTheDocument();
      expect(document.documentElement).not.toHaveClass("dark");
      expect(document.documentElement.style.colorScheme).toBe("light");
    });
  });
});
