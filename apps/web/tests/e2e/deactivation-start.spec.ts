import { expect, test } from "@playwright/test";

import { readFile, writeFile } from "node:fs/promises";

type LifecycleState = {
  oldNumber: string;
  historicalCallId?: string;
};

test.use({ trace: "off" });

function requiredEnvironment(name: "E2E_API_BASE_URL" | "E2E_LOCAL_AUTH_TOKEN" | "E2E_STATE_FILE"): string {
  const value = process.env[name]?.trim();
  if (!value) {
    throw new Error(`${name} is required for the local lifecycle journey.`);
  }
  return value;
}

async function readLifecycleState(): Promise<LifecycleState> {
  const state = JSON.parse(await readFile(requiredEnvironment("E2E_STATE_FILE"), "utf8")) as LifecycleState;
  if (typeof state.oldNumber !== "string" || !state.oldNumber.startsWith("+339")) {
    throw new Error("The local lifecycle state is missing its original fake number.");
  }
  return state;
}

function bearerHeaders(token: string): Record<string, string> {
  return { Authorization: `Bearer ${token}` };
}

test("starts deactivation and stops service while an owner call drains", async ({ page, request }) => {
  const state = await readLifecycleState();
  const apiBaseUrl = requiredEnvironment("E2E_API_BASE_URL");
  const token = requiredEnvironment("E2E_LOCAL_AUTH_TOKEN");
  const headers = bearerHeaders(token);

  const started = await request.post(`${apiBaseUrl}/api/development/call-drain-fixture/start`, { headers });
  if (!started.ok()) {
    throw new Error(`The call-drain fixture could not start (status ${started.status()}).`);
  }
  const payload = (await started.json()) as { call_id?: unknown };
  if (typeof payload.call_id !== "string") {
    throw new Error("The call-drain fixture returned an invalid call identifier.");
  }
  const historicalCallId = payload.call_id;

  await page.goto("/dashboard/calls");
  await expect(page.locator(`a[href="/dashboard/calls/${historicalCallId}"]`)).toBeVisible();
  await writeFile(
    requiredEnvironment("E2E_STATE_FILE"),
    `${JSON.stringify({ oldNumber: state.oldNumber, historicalCallId })}\n`,
    {
      encoding: "utf8",
      mode: 0o600,
    },
  );

  await page.goto("/dashboard/account");
  const openDeactivation = page.getByRole("button", {
    name: "Deactivate Opevo",
  });
  const confirmation = page.getByLabel("Type DEACTIVATE to confirm");
  await expect(async () => {
    if (!(await confirmation.isVisible())) {
      await openDeactivation.click();
    }
    await expect(confirmation).toBeVisible({ timeout: 2_000 });
  }).toPass({ timeout: 30_000 });
  await confirmation.fill("DEACTIVATE");
  await page.getByRole("button", { name: "Deactivate account" }).click();

  await expect(page).toHaveURL(/\/dashboard\/account$/);
  await expect(page.getByText("Opevo is no longer accepting new calls", { exact: true }).first()).toBeVisible();
  await expect
    .poll(
      async () => {
        const account = await request.get(`${apiBaseUrl}/api/account`, {
          headers,
        });
        if (!account.ok()) return `http-${account.status()}`;
        const accountState = (await account.json()) as {
          deactivation?: { state?: unknown };
        };
        return accountState.deactivation?.state;
      },
      {
        message: "account cleanup did not stop at active-call drainage",
        timeout: 60_000,
        intervals: [1_000, 2_000, 5_000],
      },
    )
    .toBe("draining_call");
  await page.reload();
  await expect(page.getByText("Waiting for an active call to finish")).toBeVisible();

  const blockedRouting = await request.post(`${apiBaseUrl}/api/activation/open-verification-window`, { headers });
  expect(blockedRouting.status()).toBe(409);
  expect(await blockedRouting.json()).toEqual({
    detail: { code: "account_deactivating" },
  });

  await page.goto("/activate");
  await expect(page).toHaveURL(/\/dashboard\/account$/);
  await expect(page.getByText("Opevo is no longer accepting new calls", { exact: true }).first()).toBeVisible();
});
