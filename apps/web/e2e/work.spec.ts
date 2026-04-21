import { expect, test } from "@playwright/test";

import {
  expectClearedStoredToken,
  mockShellReadApis,
  openAuthedView,
  seedAuthenticatedSession,
} from "./helpers";

test("keeps primary nav route-aware and settings anchored in the footer", async ({
  page,
}) => {
  const routes = [
    { href: "/pipelines", label: "Pipelines" },
    { href: "/work", label: "Work" },
    { href: "/graph", label: "Graph" },
    { href: "/decisions", label: "Decisions" },
    { href: "/learnings", label: "Learnings" },
  ] as const;

  await openAuthedView(page, "/pipelines");

  for (const route of routes) {
    await page.goto(route.href);
    await expect(
      page.locator(`.aq-nav-link.is-active[href="${route.href}"]`),
    ).toHaveCount(1);
    await expect(
      page.getByRole("link", { name: /^Settings$/i }),
    ).toBeVisible();
  }
});

test("returns expired tokens to the login shell", async ({ page }) => {
  await seedAuthenticatedSession(page, {
    sessionJson: { error: "Token expired." },
    sessionStatus: 401,
  });
  await mockShellReadApis(page);

  await page.goto("/pipelines");

  await expect(
    page.getByRole("heading", {
      level: 1,
      name: "Paste an AgenticQueue API key",
    }),
  ).toBeVisible();
  await expect(page.locator(".aq-auth-error")).toContainText("Token expired.");

  const stored = await expectClearedStoredToken(page);
  expect(stored.localToken).toBeNull();
  expect(stored.sessionToken).toBeNull();
});

test("renders the work queue detail panel when AQ-104 is live", async ({
  page,
}) => {
  await openAuthedView(page, "/work");

  if (!(await page.locator(".aq-table-work").count())) {
    test.skip(true, "AQ-104 is not live yet; /work still uses the placeholder shell.");
  }

  await expect(page.locator(".aq-table-work")).toBeVisible();

  const firstRow = page.locator(".aq-table-row").first();
  await expect(firstRow).toBeVisible();
  await firstRow.click();

  const detail = page.locator(".aq-detail");
  await expect(detail).toBeVisible();
  await expect(detail.getByText("Properties")).toBeVisible();
  await expect(
    detail.getByRole("button", { name: /approve/i }),
  ).toHaveCount(0);
  await expect(
    detail.getByRole("button", { name: /reject/i }),
  ).toHaveCount(0);
});
