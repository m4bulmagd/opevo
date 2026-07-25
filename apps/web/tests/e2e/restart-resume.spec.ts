import { expect, test } from "@playwright/test";

import { readFile } from "node:fs/promises";

type LifecycleState = {
  oldNumber: string;
  historicalCallId: string;
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
  if (
    typeof state.oldNumber !== "string" ||
    !state.oldNumber.startsWith("+339") ||
    typeof state.historicalCallId !== "string"
  ) {
    throw new Error("The local lifecycle state is incomplete.");
  }
  return state;
}

function normalizeDisplayedNumber(value: string): string {
  return `+${value.replace(/\D/g, "")}`;
}

function bearerHeaders(token: string): Record<string, string> {
  return { Authorization: `Bearer ${token}` };
}

test("resumes deactivation after restart, preserves history, and provisions a new number", async ({
  page,
  request,
}) => {
  test.skip(
    process.env.E2E_AFTER_SERVICE_RESTART !== "true",
    "This phase is selected by the disposable runner only after it restarts the API and worker.",
  );

  const state = await readLifecycleState();
  const apiBaseUrl = requiredEnvironment("E2E_API_BASE_URL");
  const headers = bearerHeaders(requiredEnvironment("E2E_LOCAL_AUTH_TOKEN"));

  const finished = await request.post(`${apiBaseUrl}/api/development/call-drain-fixture/finish`, {
    headers,
    data: { call_id: state.historicalCallId },
  });
  if (!finished.ok()) {
    throw new Error(`The call-drain fixture could not finish (status ${finished.status()}).`);
  }
  expect(await finished.json()).toEqual({ call_id: state.historicalCallId });

  await expect
    .poll(
      async () => {
        const account = await request.get(`${apiBaseUrl}/api/account`, { headers });
        if (!account.ok()) return `http-${account.status()}`;
        return ((await account.json()) as { status?: unknown }).status;
      },
      {
        message: "account deactivation did not complete after the active call drained",
        timeout: 120_000,
        intervals: [1_000, 2_000, 5_000],
      },
    )
    .toBe("inactive");

  await page.goto("/dashboard/account");
  await expect(page.getByRole("heading", { name: "Presvo is inactive" })).toBeVisible();

  await page.goto("/dashboard/calls");
  await expect(page.locator(`a[href="/dashboard/calls/${state.historicalCallId}"]`)).toBeVisible();
  await expect(page.getByText("completed", { exact: true })).toBeVisible();

  await page.goto("/dashboard/account");
  await page.getByRole("button", { name: "Reactivate Presvo" }).click();
  await expect(page).toHaveURL(/\/activate(?:\?|$)/, { timeout: 60_000 });

  await expect(page.getByRole("heading", { name: "Choose your Presvo number" })).toBeVisible();
  await expect(page.getByText("Your plan is ready")).toBeVisible();
  const reviewProvisioning = page.getByRole("button", { name: "Review number provisioning" });
  const confirmProvisioning = page.getByRole("button", { name: "Confirm and provision my number" });
  await expect(async () => {
    if (!(await confirmProvisioning.isVisible())) {
      await reviewProvisioning.click();
    }
    await expect(confirmProvisioning).toBeVisible({ timeout: 2_000 });
  }).toPass({ timeout: 30_000 });
  await confirmProvisioning.click();
  const newAssignedNumber = page.getByText(/^\+33\s*9/).first();
  await expect(newAssignedNumber).toBeVisible({ timeout: 60_000 });
  expect(normalizeDisplayedNumber(await newAssignedNumber.innerText())).not.toBe(state.oldNumber);
});
