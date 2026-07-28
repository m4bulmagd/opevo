import { expect, type Page, test } from "@playwright/test";

test.describe.configure({ mode: "serial" });

const BASE_URL = process.env.E2E_BASE_URL ?? "http://127.0.0.1:3300";
const AGENT_NAME = "Lea";

type ThemeMode = "dark" | "light";
type ViewportMode = "desktop" | "mobile";

type VisualCase = {
  name: string;
  theme: ThemeMode;
  viewport: { height: number; width: number };
  viewportMode: ViewportMode;
};

const VISUAL_CASES: readonly VisualCase[] = [
  {
    name: "dashboard-desktop-light.png",
    theme: "light",
    viewport: { width: 1440, height: 1100 },
    viewportMode: "desktop",
  },
  {
    name: "dashboard-desktop-dark.png",
    theme: "dark",
    viewport: { width: 1440, height: 1100 },
    viewportMode: "desktop",
  },
  {
    name: "dashboard-mobile-light.png",
    theme: "light",
    viewport: { width: 390, height: 844 },
    viewportMode: "mobile",
  },
  {
    name: "dashboard-mobile-dark.png",
    theme: "dark",
    viewport: { width: 390, height: 844 },
    viewportMode: "mobile",
  },
];

async function setThemeBeforeNavigation(page: Page, theme: ThemeMode) {
  await page.context().clearCookies({ name: "theme_mode" });
  await page.context().addCookies([{ name: "theme_mode", value: theme, url: BASE_URL }]);
}

async function expectResolvedTheme(page: Page, theme: ThemeMode) {
  const root = page.locator("html");

  await expect(root).toHaveAttribute("data-theme-mode", theme);
  if (theme === "dark") {
    await expect(root).toHaveClass(/(?:^|\s)dark(?:\s|$)/);
  } else {
    await expect(root).not.toHaveClass(/(?:^|\s)dark(?:\s|$)/);
  }
  await expect.poll(() => root.evaluate((element) => getComputedStyle(element).colorScheme)).toContain(theme);
}

async function expectDashboardReady(page: Page, viewportMode: ViewportMode) {
  await page.addStyleTag({
    content: `
      nextjs-portal { display: none !important; }
      [data-visual-dynamic="true"] {
        display: inline-block !important;
        inline-size: 11rem !important;
        overflow: hidden !important;
        white-space: nowrap !important;
      }
    `,
  });
  await expect(page.getByRole("heading", { level: 1, name: "Operations overview" })).toBeVisible();
  await expect(page.getByRole("heading", { level: 2, name: `${AGENT_NAME} is answering calls` })).toBeVisible();
  await expect(page.getByText("Live", { exact: true })).toBeVisible();
  await expect(page.getByRole("region", { name: "Operational metrics" })).toBeVisible();

  const sidebar = page.getByRole("complementary", { name: "Workspace sidebar" });
  const desktopNavigation = page.getByRole("navigation", { exact: true, name: "Workspace navigation" });
  const mobileTrigger = page.getByRole("button", { name: "Open navigation" });

  if (viewportMode === "desktop") {
    await expect(sidebar).toBeVisible();
    await expect(sidebar).toHaveCSS("width", "256px");
    await expect(desktopNavigation.getByRole("link", { name: AGENT_NAME })).toBeVisible();
    await expect(mobileTrigger).toBeHidden();
  } else {
    await expect(sidebar).toBeHidden();
    await expect(mobileTrigger).toBeVisible();
    await expect(page.getByRole("navigation", { name: "Mobile workspace destinations" })).toBeHidden();
  }

  await expect(page.getByRole("navigation", { name: "Mobile workspace navigation" })).toHaveCount(0);
  await expect(page.getByRole("banner").getByRole("link", { name: "Live call" })).toContainText("Preview");
  await expect(page.getByRole("button", { name: "Notifications (3 unread)" })).toBeVisible();
  await page.evaluate(() => document.fonts.ready);
}

async function openAndVerifyMobileNavigation(page: Page) {
  const trigger = page.getByRole("button", { name: "Open navigation" });
  await trigger.click();
  const dialog = page.getByRole("dialog", { name: "Workspace navigation" });
  const navigation = dialog.getByRole("navigation", { name: "Mobile workspace destinations" });

  await expect(navigation.getByRole("link", { name: "Overview" })).toBeFocused();
  await expect(navigation.getByRole("link", { name: "Live call" })).toContainText("Preview");
  await expect(navigation.getByRole("link", { name: "Usage & Billing" })).toBeVisible();
  await expect(navigation.getByRole("link", { name: "Account" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await expect(trigger).toBeFocused();
}

for (const visualCase of VISUAL_CASES) {
  test(`matches ${visualCase.name}`, async ({ page }) => {
    await page.setViewportSize(visualCase.viewport);
    await setThemeBeforeNavigation(page, visualCase.theme);
    await page.goto("/dashboard");
    await expectResolvedTheme(page, visualCase.theme);
    await expectDashboardReady(page, visualCase.viewportMode);

    await expect(page).toHaveScreenshot(visualCase.name, {
      animations: "disabled",
      caret: "hide",
      fullPage: false,
      mask: [page.locator('[data-visual-dynamic="true"]')],
    });

    if (visualCase.name === "dashboard-mobile-dark.png") {
      await openAndVerifyMobileNavigation(page);
    }
  });
}

test("keeps masked date geometry stable across representative calendar labels", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await setThemeBeforeNavigation(page, "light");
  await page.goto("/dashboard");
  await expectDashboardReady(page, "mobile");

  const dateContext = page.locator('[data-visual-dynamic="true"]');
  const widths: number[] = [];
  for (const label of ["Monday, May 4 · Europe/Paris", "Wednesday, September 30 · Europe/Paris"]) {
    await dateContext.evaluate((element, value) => {
      element.textContent = value;
    }, label);
    widths.push(await dateContext.evaluate((element) => element.getBoundingClientRect().width));
  }

  expect(widths[0]).toBe(widths[1]);
});

type MotionViolation = {
  animationDuration: string;
  animationName: string;
  element: string;
  transitionDuration: string;
  transitionProperty: string;
};

async function visibleMotionViolations(page: Page): Promise<MotionViolation[]> {
  return page.locator("body *").evaluateAll((elements) => {
    const parseTime = (value: string) => {
      const trimmed = value.trim();
      if (trimmed.endsWith("ms")) return Number.parseFloat(trimmed) / 1000;
      if (trimmed.endsWith("s")) return Number.parseFloat(trimmed);
      return 0;
    };
    const list = (value: string) => value.split(",").map((part) => part.trim());
    const describe = (element: Element) => {
      const htmlElement = element as HTMLElement;
      const id = htmlElement.id ? `#${htmlElement.id}` : "";
      const classes =
        typeof htmlElement.className === "string"
          ? htmlElement.className
              .trim()
              .split(/\s+/)
              .filter(Boolean)
              .slice(0, 3)
              .map((className) => `.${className}`)
              .join("")
          : "";
      const accessibleName =
        htmlElement.getAttribute("aria-label") ?? htmlElement.textContent?.replace(/\s+/g, " ").trim().slice(0, 80);
      return `${htmlElement.tagName.toLowerCase()}${id}${classes}${accessibleName ? ` (“${accessibleName}”)` : ""}`;
    };

    return elements.flatMap((element) => {
      const htmlElement = element as HTMLElement;
      const style = getComputedStyle(htmlElement);
      const rect = htmlElement.getBoundingClientRect();
      const visible =
        style.display !== "none" &&
        style.visibility !== "hidden" &&
        Number.parseFloat(style.opacity) > 0 &&
        rect.width > 0 &&
        rect.height > 0;
      if (!visible) return [];

      const animationNames = list(style.animationName);
      const animationDurations = list(style.animationDuration).map(parseTime);
      const hasAnimation = animationNames.some(
        (name, index) => name !== "none" && (animationDurations[index % animationDurations.length] ?? 0) > 0,
      );

      const transitionProperties = list(style.transitionProperty);
      const transitionDurations = list(style.transitionDuration).map(parseTime);
      const hasTransformTransition = transitionProperties.some(
        (property, index) =>
          (property === "all" || property === "transform") &&
          (transitionDurations[index % transitionDurations.length] ?? 0) > 0,
      );

      if (!hasAnimation && !hasTransformTransition) return [];

      return [
        {
          animationDuration: style.animationDuration,
          animationName: style.animationName,
          element: describe(htmlElement),
          transitionDuration: style.transitionDuration,
          transitionProperty: style.transitionProperty,
        },
      ];
    });
  });
}

test("reduced motion keeps workspace route changes static", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1100 });
  await page.emulateMedia({ reducedMotion: "reduce" });
  await setThemeBeforeNavigation(page, "light");
  await page.goto("/dashboard");
  await expectDashboardReady(page, "desktop");

  const navigation = page.getByRole("navigation", { exact: true, name: "Workspace navigation" });
  const destinations = [
    { heading: "Operations overview", link: "Overview" },
    { heading: "Calls", link: "Calls" },
    { heading: AGENT_NAME, link: AGENT_NAME },
  ] as const;

  for (const destination of destinations) {
    await navigation.getByRole("link", { name: destination.link }).click();
    await expect(page.getByRole("heading", { level: 1, name: destination.heading })).toBeVisible();
    const current = navigation.getByRole("link", { name: destination.link });
    await expect(current).toHaveAttribute("aria-current", "page");
    await expect(current.locator('[data-slot="active-navigation-marker"]')).toHaveCount(0);
    await page.evaluate(() => document.fonts.ready);

    const violations = await visibleMotionViolations(page);
    expect(
      violations,
      `Reduced-motion violations on ${destination.link}:\n${JSON.stringify(violations, null, 2)}`,
    ).toEqual([]);
  }
});

for (const viewport of [
  { width: 390, height: 844 },
  { width: 768, height: 1024 },
  { width: 1024, height: 768 },
  { width: 1440, height: 1100 },
] as const) {
  test(`has no horizontal overflow at ${viewport.width}×${viewport.height}`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await setThemeBeforeNavigation(page, "light");
    await page.goto("/dashboard");
    await expectDashboardReady(page, viewport.width < 1024 ? "mobile" : "desktop");

    const overflow = await page.evaluate(() => ({
      bodyClientWidth: document.body.clientWidth,
      bodyScrollWidth: document.body.scrollWidth,
      rootClientWidth: document.documentElement.clientWidth,
      rootScrollWidth: document.documentElement.scrollWidth,
    }));
    expect(overflow, `Horizontal overflow at ${viewport.width}×${viewport.height}`).toEqual({
      bodyClientWidth: viewport.width,
      bodyScrollWidth: viewport.width,
      rootClientWidth: viewport.width,
      rootScrollWidth: viewport.width,
    });

    const sidebar = page.getByRole("complementary", { name: "Workspace sidebar" });
    const mobileTrigger = page.getByRole("button", { name: "Open navigation" });
    if (viewport.width < 1024) {
      await expect(mobileTrigger).toBeVisible();
      await expect(sidebar).toBeHidden();
    } else {
      await expect(mobileTrigger).toBeHidden();
      await expect(sidebar).toBeVisible();
      await expect(sidebar).toHaveCSS("width", "256px");
    }
    await expect(page.locator('nav[aria-label="Mobile workspace navigation"]')).toHaveCount(0);
  });
}
