import { expect, type Page, test } from "@playwright/test";

test.describe.configure({ mode: "serial" });

const BASE_URL = process.env.E2E_BASE_URL ?? "http://127.0.0.1:3300";

type VisualCase = {
  name: string;
  route: "/" | "/activate";
  viewport: { height: number; width: number };
};

const VISUAL_CASES: readonly VisualCase[] = [
  {
    name: "landing-desktop-light.png",
    route: "/",
    viewport: { width: 1440, height: 1100 },
  },
  {
    name: "landing-mobile-light.png",
    route: "/",
    viewport: { width: 390, height: 844 },
  },
  {
    name: "activation-desktop-light.png",
    route: "/activate",
    viewport: { width: 1440, height: 1100 },
  },
  {
    name: "activation-mobile-light.png",
    route: "/activate",
    viewport: { width: 390, height: 844 },
  },
];

async function setLightTheme(page: Page) {
  await page.context().clearCookies({ name: "theme_mode" });
  await page.context().addCookies([{ name: "theme_mode", value: "light", url: BASE_URL }]);
}

async function expectNoHorizontalOverflow(page: Page, viewportWidth: number) {
  const overflow = await page.evaluate(() => ({
    body: document.body.scrollWidth,
    root: document.documentElement.scrollWidth,
  }));

  expect(overflow).toEqual({ body: viewportWidth, root: viewportWidth });
}

async function expectLandingReady(page: Page) {
  await expect(page.getByRole("heading", { level: 1, name: /missed call/i })).toBeVisible();
  await expect(page.getByText("Built for French businesses")).toBeVisible();
  await expect(page.getByRole("region", { name: "Product overview" })).toBeVisible();
  await expect(page.getByText("Preview", { exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: /Dashboard|Open dashboard/ }).first()).toBeVisible();
}

async function expectActivationReady(page: Page) {
  await expect(page.getByRole("heading", { level: 1, name: "Tell us about your business" })).toBeVisible();
  await expect(page.getByText("Step 1 of 5")).toBeVisible();
  await expect(page.locator('[data-slot="activation-progress-segment"]')).toHaveCount(5);
  await expect(page.locator('[data-slot="activation-step-card"]').getByText("Local development")).toBeVisible();
  await expect(page.getByLabel("Owner name")).toBeVisible();
  await expect(page.getByLabel("Business name")).toBeVisible();
  await expect(page.getByLabel("Existing French number")).toBeVisible();

  const continueButton = page.getByRole("button", { name: "Continue" });
  await expect(continueButton).toBeAttached();
  const height = await continueButton.evaluate((element) => element.getBoundingClientRect().height);
  expect(height).toBeGreaterThanOrEqual(44);
}

for (const visualCase of VISUAL_CASES) {
  test(`matches ${visualCase.name}`, async ({ page }) => {
    await page.setViewportSize(visualCase.viewport);
    await setLightTheme(page);
    await page.goto(visualCase.route);
    await page.addStyleTag({ content: "nextjs-portal { display: none !important; }" });

    if (visualCase.route === "/") {
      await expectLandingReady(page);
    } else {
      await expectActivationReady(page);
    }

    await page.evaluate(() => document.fonts.ready);
    await expectNoHorizontalOverflow(page, visualCase.viewport.width);
    await expect(page).toHaveScreenshot(visualCase.name, {
      animations: "disabled",
      caret: "hide",
      fullPage: false,
    });
  });
}
