import { expect, type Page, test } from "@playwright/test";

const BASE_URL = process.env.E2E_BASE_URL ?? "http://127.0.0.1:3300";
const CALL_ID = "11111111-1111-4111-8111-111111111111";
const LIVE_CALL_VISUAL_TIME = new Date("2026-07-29T12:00:00.000Z");

type ThemeMode = "dark" | "light";

type VisualCase = {
  name: string;
  route: string;
  theme: ThemeMode;
  viewport: { height: number; width: number };
  heading: string;
};

const VISUAL_CASES: readonly VisualCase[] = [
  {
    name: "calls-desktop-light.png",
    route: "/dashboard/calls",
    theme: "light",
    viewport: { width: 1440, height: 1100 },
    heading: "Calls",
  },
  {
    name: "calls-mobile-light.png",
    route: "/dashboard/calls",
    theme: "light",
    viewport: { width: 390, height: 844 },
    heading: "Calls",
  },
  {
    name: "call-detail-desktop-light.png",
    route: `/dashboard/calls/${CALL_ID}`,
    theme: "light",
    viewport: { width: 1440, height: 1100 },
    heading: "+33612345678",
  },
  {
    name: "call-detail-mobile-light.png",
    route: `/dashboard/calls/${CALL_ID}`,
    theme: "light",
    viewport: { width: 390, height: 844 },
    heading: "+33612345678",
  },
  {
    name: "live-call-preview-desktop-light.png",
    route: "/dashboard/live-call",
    theme: "light",
    viewport: { width: 1440, height: 1100 },
    heading: "Live call",
  },
  {
    name: "live-call-preview-mobile-dark.png",
    route: "/dashboard/live-call",
    theme: "dark",
    viewport: { width: 390, height: 844 },
    heading: "Live call",
  },
];

async function setThemeBeforeNavigation(page: Page, theme: ThemeMode) {
  await page.context().clearCookies({ name: "theme_mode" });
  await page.context().addCookies([{ name: "theme_mode", value: theme, url: BASE_URL }]);
}

async function prepareRoute(page: Page, visualCase: VisualCase) {
  await page.setViewportSize(visualCase.viewport);
  await setThemeBeforeNavigation(page, visualCase.theme);
  if (visualCase.route === "/dashboard/live-call") {
    await page.clock.install({ time: LIVE_CALL_VISUAL_TIME });
    await page.clock.pauseAt(LIVE_CALL_VISUAL_TIME);
  }
  await page.goto(visualCase.route);
  await page.addStyleTag({ content: "nextjs-portal { display: none !important; }" });
  await expect(page.getByRole("heading", { level: 1, name: visualCase.heading })).toBeVisible();
  await page.evaluate(() => document.fonts.ready);
}

async function expectNoHorizontalOverflow(page: Page, viewportWidth: number) {
  const overflow = await page.evaluate(() => ({
    bodyClientWidth: document.body.clientWidth,
    bodyScrollWidth: document.body.scrollWidth,
    rootClientWidth: document.documentElement.clientWidth,
    rootScrollWidth: document.documentElement.scrollWidth,
  }));

  expect(overflow).toEqual({
    bodyClientWidth: viewportWidth,
    bodyScrollWidth: viewportWidth,
    rootClientWidth: viewportWidth,
    rootScrollWidth: viewportWidth,
  });
}

for (const visualCase of VISUAL_CASES) {
  test(`matches ${visualCase.name}`, async ({ page }) => {
    await prepareRoute(page, visualCase);
    await expectNoHorizontalOverflow(page, visualCase.viewport.width);

    if (visualCase.route === "/dashboard/calls") {
      await expect(page.getByRole("table", { name: "Call history" })).toBeVisible();
      await expect(page.getByText("4 matching calls")).toBeVisible();
    }
    if (visualCase.route.includes(CALL_ID)) {
      await expect(page.getByRole("region", { name: "Generated summary" })).toBeVisible();
      await expect(page.getByRole("region", { name: "Full transcript" })).toBeVisible();
    }
    if (visualCase.route === "/dashboard/live-call") {
      const callOverview = page.getByRole("region", { name: "Preview call overview" });
      await expect(page.getByRole("main").getByText("Preview", { exact: true })).toBeVisible();
      await expect(callOverview.getByText("01:42", { exact: true })).toBeVisible();
      await expect(page.getByRole("note")).toContainText("Nothing here places, answers, or ends a real call");
    }

    await expect(page).toHaveScreenshot(visualCase.name, {
      animations: "disabled",
      caret: "hide",
      fullPage: false,
    });
  });
}

test("submits call filters and restores URL-owned state through browser history", async ({ page }) => {
  await setThemeBeforeNavigation(page, "light");
  await page.goto("/dashboard/calls");
  await expect(page.getByText("4 matching calls")).toBeVisible();
  const filters = page.locator("form").filter({ has: page.locator("#call-search") });

  await page.locator("#call-search").fill("appointment");
  await filters.getByRole("combobox", { name: "Filter by status" }).selectOption("completed");
  await filters.getByRole("combobox", { name: "Filter by date" }).selectOption("30d");
  await filters.getByRole("button", { name: "Apply filters" }).click();

  await expect(page).toHaveURL(/\/dashboard\/calls\?q=appointment&status=completed&range=30d$/);
  await expect(page.locator("#call-search")).toHaveValue("appointment");
  await expect(filters.getByRole("combobox", { name: "Filter by status" })).toHaveValue("completed");
  await expect(filters.getByRole("combobox", { name: "Filter by date" })).toHaveValue("30d");

  await page.goBack();
  await expect(page).toHaveURL(/\/dashboard\/calls$/);
  await expect(page.locator("#call-search")).toHaveValue("");
  await expect(filters.getByRole("combobox", { name: "Filter by status" })).toHaveValue("all");

  await page.goForward();
  await expect(page.locator("#call-search")).toHaveValue("appointment");
  await expect(filters.getByRole("combobox", { name: "Filter by date" })).toHaveValue("30d");
});

test("searches the stored transcript locally", async ({ page }) => {
  await page.goto(`/dashboard/calls/${CALL_ID}`);
  const transcript = page.getByRole("region", { name: "Full transcript" });

  await expect(transcript.getByRole("listitem")).toHaveCount(4);
  await transcript.getByRole("searchbox", { name: "Search transcript" }).fill("jeudi");
  await expect(transcript.getByRole("listitem")).toHaveCount(2);
  await expect(transcript.getByText("jeudi", { exact: false })).toHaveCount(2);
  await transcript.getByRole("searchbox", { name: "Search transcript" }).fill("phrase absente");
  await expect(transcript.getByText(/No transcript lines match/i)).toBeVisible();
});

test("keeps every Preview interaction local after navigation", async ({ page }) => {
  await page.goto("/dashboard/live-call");
  await expect(page.getByRole("heading", { level: 1, name: "Live call" })).toBeVisible();
  await page.waitForLoadState("networkidle");

  const observedApiRequests: string[] = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.pathname.startsWith("/api/") || url.port === "5800") {
      observedApiRequests.push(request.url());
    }
  });

  const transcript = page.getByRole("region", { name: "Live transcript" });
  await transcript.getByRole("button", { name: "Connecting" }).click();
  await expect(page.getByRole("region", { name: "Preview call overview" }).getByText("Connecting")).toBeVisible();
  await transcript.getByRole("button", { name: "Active" }).click();

  await page.getByRole("textbox", { name: "Preview call notes" }).fill("Confirm Thursday afternoon.");
  await page.getByRole("button", { name: "Save preview note" }).click();
  await expect(page.getByRole("status", { name: "Preview note status" })).toHaveText("Saved in this preview only");

  await page.getByRole("button", { name: "End preview" }).click();
  await expect(transcript.getByText("Preview completed")).toBeVisible();
  await page.getByRole("button", { name: "Restart preview" }).click();
  await expect(page.getByRole("textbox", { name: "Preview call notes" })).toHaveValue("");
  await expect(page.getByRole("region", { name: "Preview call overview" }).getByText("Active")).toBeVisible();
  expect(observedApiRequests).toEqual([]);
});

for (const viewport of [
  { width: 390, height: 844 },
  { width: 768, height: 1024 },
  { width: 1024, height: 768 },
  { width: 1440, height: 1100 },
] as const) {
  test(`keeps migrated call routes within ${viewport.width}px`, async ({ page }) => {
    await page.setViewportSize(viewport);
    for (const route of ["/dashboard/calls", `/dashboard/calls/${CALL_ID}`, "/dashboard/live-call"]) {
      await page.goto(route);
      await expect(page.getByRole("main")).toBeVisible();
      await expectNoHorizontalOverflow(page, viewport.width);
    }
  });
}
