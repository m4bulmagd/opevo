import { expect, type Page, test } from "@playwright/test";

test.describe.configure({ mode: "serial" });

const BASE_URL = process.env.E2E_BASE_URL ?? "http://127.0.0.1:3300";

type ThemeMode = "dark" | "light";

type VisualCase = {
  heading: string;
  name: string;
  route: string;
  theme: ThemeMode;
  viewport: { height: number; width: number };
};

const VISUAL_CASES: readonly VisualCase[] = [
  {
    name: "assistant-desktop-light.png",
    route: "/dashboard/agent",
    theme: "light",
    viewport: { width: 1440, height: 1100 },
    heading: "Assistant",
  },
  {
    name: "assistant-preview-mobile-dark.png",
    route: "/dashboard/agent?tab=preview",
    theme: "dark",
    viewport: { width: 390, height: 844 },
    heading: "Assistant",
  },
  {
    name: "billing-desktop-light.png",
    route: "/dashboard/billing",
    theme: "light",
    viewport: { width: 1440, height: 1100 },
    heading: "Usage & billing",
  },
  {
    name: "billing-mobile-dark.png",
    route: "/dashboard/billing",
    theme: "dark",
    viewport: { width: 390, height: 844 },
    heading: "Usage & billing",
  },
  {
    name: "account-desktop-light.png",
    route: "/dashboard/account",
    theme: "light",
    viewport: { width: 1440, height: 1100 },
    heading: "Settings",
  },
  {
    name: "account-mobile-dark.png",
    route: "/dashboard/account",
    theme: "dark",
    viewport: { width: 390, height: 844 },
    heading: "Settings",
  },
];

async function setThemeBeforeNavigation(page: Page, theme: ThemeMode) {
  await page.context().clearCookies({ name: "theme_mode" });
  await page.context().addCookies([{ name: "theme_mode", value: theme, url: BASE_URL }]);
}

async function prepareRoute(page: Page, visualCase: VisualCase) {
  await page.setViewportSize(visualCase.viewport);
  await setThemeBeforeNavigation(page, visualCase.theme);
  await page.goto(visualCase.route);
  await page.addStyleTag({
    content: `
      nextjs-portal { display: none !important; }
      [data-visual-billing-date="true"] {
        display: inline-block !important;
        inline-size: 8.5rem !important;
        overflow: hidden !important;
        white-space: nowrap !important;
      }
    `,
  });
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

function observeBackendRequests(page: Page) {
  const requests: string[] = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.port === "5800" || url.pathname.startsWith("/api/")) {
      requests.push(`${request.method()} ${url.pathname}`);
    }
  });
  return requests;
}

async function confirmAccountProfileSave(page: Page) {
  const unsavedChanges = page.getByRole("status", { name: "Unsaved changes" });
  await expect(unsavedChanges).toBeVisible();
  const confirmedSaveResponse = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return response.request().method() === "POST" && url.pathname === "/dashboard/account";
  });

  await unsavedChanges.getByRole("button", { name: "Save changes" }).click();

  expect((await confirmedSaveResponse).ok()).toBe(true);
  await expect(unsavedChanges).toHaveCount(0);
}

for (const visualCase of VISUAL_CASES) {
  test(`matches ${visualCase.name}`, async ({ page }) => {
    await prepareRoute(page, visualCase);
    await expectNoHorizontalOverflow(page, visualCase.viewport.width);
    const screenshotMasks = [page.locator('[data-visual-billing-date="true"]')];

    if (visualCase.route.startsWith("/dashboard/agent")) {
      await expect(page.getByRole("tab", { name: "Advanced Preview" })).toBeVisible();
    }
    if (visualCase.route.includes("tab=preview")) {
      await expect(page.getByRole("region", { name: "Advanced assistant Preview" })).toBeVisible();
    }
    if (visualCase.route === "/dashboard/billing") {
      await expect(page.getByRole("region", { name: "Current period usage" })).toBeVisible();
      await expect(page.getByRole("region", { name: "Plan comparison Preview" })).toBeVisible();
    }
    if (visualCase.route === "/dashboard/account") {
      for (const regionName of [
        "Profile",
        "Assigned number",
        "Account status",
        "Notifications Preview",
        "Privacy & recordings Preview",
        "Security",
        "Danger zone",
      ]) {
        await expect(page.getByRole("region", { name: regionName })).toBeVisible();
      }
      await expect(page.getByRole("status", { name: "Unsaved changes" })).toHaveCount(0);
      screenshotMasks.push(page.getByRole("region", { name: "Assigned number" }).locator("p").first());
    }

    await expect(page).toHaveScreenshot(visualCase.name, {
      animations: "disabled",
      caret: "hide",
      fullPage: true,
      mask: screenshotMasks,
    });
  });
}

test("discards, persists, and restores the live account Profile name", async ({ page }) => {
  await page.goto("/dashboard/account");
  await page.waitForLoadState("networkidle");
  const fullName = page.getByRole("textbox", { name: "Full name" });
  const initialFullName = await fullName.inputValue();
  const temporaryFullName =
    initialFullName === "Presvo Profile E2E" ? "Presvo Profile E2E alternate" : "Presvo Profile E2E";
  let restorationConfirmed = false;

  try {
    await fullName.fill(temporaryFullName);
    await expect(page.getByRole("status", { name: "Unsaved changes" })).toBeVisible();
    await page.getByRole("button", { name: "Discard" }).click();
    await expect(fullName).toHaveValue(initialFullName);

    await fullName.fill(temporaryFullName);
    await confirmAccountProfileSave(page);
    await expect(fullName).toHaveValue(temporaryFullName);
    await page.reload();
    await expect(page.getByRole("textbox", { name: "Full name" })).toHaveValue(temporaryFullName);

    const persistedFullName = page.getByRole("textbox", { name: "Full name" });
    await persistedFullName.fill(initialFullName);
    await confirmAccountProfileSave(page);
    await expect(persistedFullName).toHaveValue(initialFullName);
    await page.reload();
    await expect(page.getByRole("textbox", { name: "Full name" })).toHaveValue(initialFullName);
    restorationConfirmed = true;
  } finally {
    if (!restorationConfirmed) {
      const recoveryFullName = page.getByRole("textbox", { name: "Full name" });
      if (!(await recoveryFullName.isVisible().catch(() => false))) {
        await page.goto("/dashboard/account");
        await page.waitForLoadState("networkidle");
      }
      await page.getByRole("textbox", { name: "Full name" }).fill(initialFullName);
      if ((await page.getByRole("status", { name: "Unsaved changes" }).count()) > 0) {
        await confirmAccountProfileSave(page);
      }
      await page.reload();
      await expect(page.getByRole("textbox", { name: "Full name" })).toHaveValue(initialFullName);
    }
  }
});

test("guards, discards, persists, and restores live assistant settings", async ({ page }) => {
  await setThemeBeforeNavigation(page, "light");
  await page.goto("/dashboard/agent");
  await page.waitForLoadState("networkidle");
  const agentName = page.getByRole("textbox", { name: "Agent name" });
  await expect(agentName).toHaveValue("Lea");

  await agentName.click();
  await agentName.press("Control+A");
  await agentName.pressSequentially("Lea draft");
  await expect(agentName).toHaveValue("Lea draft");
  const unsavedChanges = page.locator('[aria-label="Unsaved changes"]');
  await expect(unsavedChanges).toBeAttached();
  await unsavedChanges.scrollIntoViewIfNeeded();
  await expect(unsavedChanges).toBeVisible();

  page.once("dialog", async (dialog) => {
    expect(dialog.type()).toBe("confirm");
    expect(dialog.message()).toContain("You have unsaved changes");
    await dialog.dismiss();
  });
  await page.getByRole("link", { name: "Usage & Billing" }).click();
  await expect(page).toHaveURL(/\/dashboard\/agent$/);
  await expect(agentName).toHaveValue("Lea draft");

  await page.getByRole("button", { name: "Discard" }).click();
  await expect(agentName).toHaveValue("Lea");

  await agentName.click();
  await agentName.press("Control+A");
  await agentName.pressSequentially("Lea Verified");
  await page.getByRole("button", { name: "Save changes" }).click();
  await expect(page.getByRole("status", { name: "Save feedback" })).toHaveText("Agent settings saved.");
  await expect(unsavedChanges).toHaveCount(0);
  await page.reload();
  await expect(page.getByRole("textbox", { name: "Agent name" })).toHaveValue("Lea Verified");

  const savedAgentName = page.getByRole("textbox", { name: "Agent name" });
  await savedAgentName.click();
  await savedAgentName.press("Control+A");
  await savedAgentName.pressSequentially("Lea");
  await page.getByRole("button", { name: "Save changes" }).click();
  await expect(page.getByRole("status", { name: "Save feedback" })).toHaveText("Agent settings saved.");
  await page.reload();
  await expect(page.getByRole("textbox", { name: "Agent name" })).toHaveValue("Lea");
});

test("keeps assistant voice and test-call Preview local under reduced motion", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/dashboard/agent?tab=preview");
  await page.waitForLoadState("networkidle");
  const backendRequests = observeBackendRequests(page);
  const preview = page.getByRole("region", { name: "Advanced assistant Preview" });

  await preview.getByRole("radio", { name: /Warm/i }).check();
  await preview.getByRole("radio", { name: /Inès/i }).check();
  await preview.getByRole("button", { name: "Preview Inès locally" }).click();
  await expect(preview.getByRole("status", { name: "Voice preview status" })).toContainText("Previewing Inès locally");

  await preview.getByRole("button", { name: "Test assistant Preview" }).click();
  const dialog = page.getByRole("dialog", { name: "Test Lea" });
  await expect(dialog.getByRole("status", { name: "Preview call status" })).toContainText(/Connecting|Lea is speaking/);
  await dialog.getByRole("button", { name: "End preview" }).click();
  await expect(dialog.getByRole("status", { name: "Preview call status" })).toHaveText("Preview ended");
  await dialog.getByRole("button", { name: "Close" }).click();

  await preview.getByRole("button", { name: "Reset Preview settings" }).click();
  await expect(preview.getByRole("radio", { name: /Professional/i })).toBeChecked();
  await expect(preview.getByRole("radio", { name: /Camille/i })).toBeChecked();
  expect(backendRequests).toEqual([]);
});

test("keeps plan comparison local while exposing the real billing boundary", async ({ page }) => {
  await page.goto("/dashboard/billing");
  await page.waitForLoadState("networkidle");
  const backendRequests = observeBackendRequests(page);

  await expect(page.getByRole("button", { name: "Manage billing" })).toBeVisible();
  const preview = page.getByRole("region", { name: "Plan comparison Preview" });
  await preview.getByRole("radio", { name: /Standard/i }).check();
  await preview.getByRole("button", { name: "Compare capabilities" }).click();
  await expect(preview.getByRole("status", { name: "Plan comparison status" })).toContainText(
    "Standard selected for local comparison",
  );
  await preview.getByRole("button", { name: "Reset plan Preview" }).click();
  await expect(preview.getByRole("radio", { name: /Starter/i })).toBeChecked();
  expect(backendRequests).toEqual([]);
});

test("resets account Preview and retains exact deactivation confirmation", async ({ page }) => {
  await page.goto("/dashboard/account");
  await page.waitForLoadState("networkidle");
  const backendRequests = observeBackendRequests(page);
  const notifications = page.getByRole("region", { name: "Notifications Preview" });
  const privacy = page.getByRole("region", { name: "Privacy & recordings Preview" });
  const security = page.getByRole("region", { name: "Security" });
  const danger = page.getByRole("region", { name: "Danger zone" });

  await expect(security).toContainText("Password and sign-in methods are managed through Clerk in hosted accounts.");
  await notifications.getByRole("switch", { name: "Call summaries" }).click();
  await privacy.getByRole("combobox", { name: "Preview recording retention" }).selectOption("365");
  await security.getByRole("switch", { name: "Two-factor authentication" }).click();
  await expect(notifications.getByRole("status", { name: "Account settings Preview status" })).toContainText(
    "No account setting was updated",
  );
  await notifications.getByRole("button", { name: "Reset settings Preview" }).click();
  await expect(notifications.getByRole("switch", { name: "Call summaries" })).toBeChecked();
  await expect(privacy.getByRole("combobox", { name: "Preview recording retention" })).toHaveValue("30");
  await expect(security.getByRole("switch", { name: "Two-factor authentication" })).not.toBeChecked();

  await danger.getByRole("button", { name: "Deactivate Presvo" }).click();
  const dialog = page.getByRole("alertdialog");
  const confirmation = dialog.getByLabel("Type DEACTIVATE to confirm");
  const deactivate = dialog.getByRole("button", { name: "Deactivate account" });
  await confirmation.fill("deactivate");
  await expect(deactivate).toBeDisabled();
  await confirmation.fill("DEACTIVATE");
  await expect(deactivate).toBeEnabled();
  await dialog.getByRole("button", { name: "Keep Presvo active" }).click();
  await expect(dialog).toHaveCount(0);

  await danger.getByRole("button", { name: "Deactivate Presvo" }).click();
  const reopenedDialog = page.getByRole("alertdialog");
  await expect(reopenedDialog.getByLabel("Type DEACTIVATE to confirm")).toHaveValue("");
  await expect(reopenedDialog.getByRole("button", { name: "Deactivate account" })).toBeDisabled();
  await reopenedDialog.getByRole("button", { name: "Keep Presvo active" }).click();
  expect(backendRequests).toEqual([]);
});

for (const viewport of [
  { width: 390, height: 844 },
  { width: 768, height: 1024 },
  { width: 1024, height: 768 },
  { width: 1440, height: 1100 },
] as const) {
  test(`keeps configuration routes within ${viewport.width}px`, async ({ page }) => {
    await page.setViewportSize(viewport);
    for (const route of ["/dashboard/agent?tab=preview", "/dashboard/billing", "/dashboard/account"]) {
      await page.goto(route);
      await expect(page.getByRole("main")).toBeVisible();
      await expectNoHorizontalOverflow(page, viewport.width);
    }
  });
}
