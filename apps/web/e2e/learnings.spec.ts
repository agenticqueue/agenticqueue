import { execFileSync } from "node:child_process";

import { expect, test } from "@playwright/test";

const repoRoot = process.cwd();

function seedLearningsView() {
  return JSON.parse(
    execFileSync("uv", ["run", "python", "tests/web/seed_learnings_view.py"], {
      cwd: repoRoot,
      encoding: "utf-8",
    }),
  ) as { api_token: string };
}

test.beforeEach(async ({ page }) => {
  const seed = seedLearningsView();

  await page.addInitScript((apiToken: string) => {
    window.localStorage.setItem("aq:web:remember-token", "false");
    window.sessionStorage.setItem("aq:web:api-token", apiToken);
  }, seed.api_token);
});

test("renders the learnings browser and opens the detail panel", async ({ page }) => {
  await page.goto("/learnings");

  await expect(
    page.getByRole("heading", { level: 1, name: "Learnings" }),
  ).toBeVisible();

  const rows = page.locator("[data-testid^='learning-row-']");
  await expect(rows.first()).toBeVisible();

  const tierOneFilter = page
    .locator(".aq-filter-group", { hasText: "Tier" })
    .getByRole("button", { name: /Tier 1/i });
  await tierOneFilter.click();
  await expect(rows).toHaveCount(1);
  await expect(page.getByText(/1 visible/i)).toBeVisible();

  await rows.first().click();

  const detail = page.getByTestId("learning-detail");
  await expect(detail).toBeVisible();
  await expect(detail.getByText("Evidence")).toBeVisible();
  await expect(detail.locator("li").first()).toBeVisible();
  await expect(detail.getByText("Applied in")).toBeVisible();

  await expect(detail.getByRole("button", { name: /^Promote$/i })).toHaveCount(0);
  await expect(detail.getByRole("button", { name: /^Supersede$/i })).toHaveCount(0);
});
