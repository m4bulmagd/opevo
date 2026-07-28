import { expect, type Page, test } from "@playwright/test";

import { writeFile } from "node:fs/promises";

test.describe.configure({ mode: "serial" });

const FIXED_EXISTING_NUMBER = "01 99 00 00 00";

function stateFilePath(): string {
  const path = process.env.E2E_STATE_FILE?.trim();
  if (!path) {
    throw new Error("E2E_STATE_FILE is required for the local lifecycle journey.");
  }
  return path;
}

function normalizeDisplayedNumber(value: string): string {
  return `+${value.replace(/\D/g, "")}`;
}

async function completeBusinessMilestone(page: Page) {
  await expect(page.getByRole("heading", { name: "Tell us about your business" })).toBeVisible();
  await expect(page.locator('[data-slot="activation-progress-segment"]')).toHaveCount(5);
  await expect(page.getByText("Step 1 of 5")).toBeVisible();
  await page.getByLabel("Owner name").fill("Camille Martin");
  await page.getByLabel("Business name").fill("Atelier Martin");
  await page.getByLabel("Business type").fill("Plumbing service");

  const existingNumber = page.getByLabel("Existing French number");
  await existingNumber.fill(FIXED_EXISTING_NUMBER);
  await page.getByRole("button", { name: "Check carrier" }).click();
  await expect(page.getByText("Suggested carrier")).toBeVisible();
  await page.getByRole("button", { name: "Confirm carrier" }).click();

  await page.getByRole("button", { name: "Continue" }).click();
  await expect(page.getByRole("heading", { name: "Shape your receptionist" })).toBeVisible();
}

async function completeReceptionistMilestone(page: Page) {
  await expect(page.getByLabel("Receptionist name")).toBeVisible();
  await page.getByLabel("Receptionist name").fill("Lea");
  await page
    .getByLabel("Public description")
    .fill("Atelier Martin handles plumbing repairs and installations for customers across Paris.");
  await page.getByLabel("Special instructions").fill("Always ask for the caller's postal code.");
  await page.getByLabel("Escalation notes").fill("Escalate urgent leaks to the owner.");

  await page.getByRole("button", { name: "Continue" }).click();
  await expect(page.getByRole("heading", { name: "Choose your Presvo number" })).toBeVisible();
}

async function applyForwardingGuidance(page: Page) {
  await expect(page.getByRole("heading", { name: "Forward missed calls to Presvo" })).toBeVisible();
  await expect(page.getByText("Forward unanswered calls", { exact: true })).toBeVisible();
  await expect(page.getByText("Forward calls when your line is busy", { exact: true })).toBeVisible();
  await expect(page.getByText("Forward calls when your line is unreachable", { exact: true })).toBeVisible();
  await expect(page.getByText(/unconditional/i)).toHaveCount(0);
}

test("local owner activates Presvo without external providers", async ({ page }) => {
  await page.goto("/activate");
  await expect(page.getByText("Local development").first()).toBeVisible();

  await completeBusinessMilestone(page);
  await page.reload();
  await expect(page.getByRole("link", { name: /Business.*Complete/i })).toBeVisible();

  await completeReceptionistMilestone(page);
  await page.reload();
  await expect(page.getByRole("link", { name: /Receptionist.*Complete/i })).toBeVisible();

  await page.getByRole("button", { name: "Activate local starter plan" }).click();
  await page.getByRole("button", { name: "Review number provisioning" }).click();
  await page.getByRole("button", { name: "Confirm and provision my number" }).click();
  const assignedNumber = page.getByText(/^\+33\s*9/).first();
  await expect(assignedNumber).toBeVisible({ timeout: 60_000 });
  const oldNumber = normalizeDisplayedNumber(await assignedNumber.innerText());
  await expect(page.getByText(/conditionally forward unanswered, busy, and unreachable calls/i)).toBeVisible();
  await page.getByRole("link", { name: "Continue to forwarding" }).click();
  await expect(page.getByRole("heading", { name: "Forward missed calls to Presvo" })).toBeVisible();

  await page.reload();
  await applyForwardingGuidance(page);
  await page.getByRole("button", { name: "Start 10-minute test" }).click();
  await expect(page.getByRole("button", { name: "Simulate forwarded call" })).toBeVisible();

  await page.reload();
  await expect(page.getByRole("timer", { name: "Verification time remaining" })).toBeVisible();
  await page.getByRole("button", { name: "Simulate forwarded call" }).click();
  await expect(page.getByRole("button", { name: "Go live" })).toBeVisible();
  await page.getByRole("button", { name: "Go live" }).click();

  await expect(page).toHaveURL(/\/dashboard$/, { timeout: 60_000 });
  await expect(page.getByRole("heading", { level: 2, name: "Lea is answering calls" })).toBeVisible();

  await writeFile(stateFilePath(), `${JSON.stringify({ oldNumber })}\n`, {
    encoding: "utf8",
    mode: 0o600,
  });
});
