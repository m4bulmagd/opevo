import { expect, test } from "@playwright/test";

test("resumes the persisted active dashboard after local services restart", async ({ page }) => {
  test.skip(
    process.env.E2E_AFTER_SERVICE_RESTART !== "true",
    "This phase is selected by the disposable runner only after it restarts every local service.",
  );

  await page.goto("/dashboard");

  await expect(page).toHaveURL(/\/dashboard$/);
  await expect(page.getByText("Presvo is answering")).toBeVisible();
});
