import path from "node:path";

import { expect, test } from "@playwright/test";

import { openAuthedView } from "./helpers";

const HEADER_SCREENSHOT_PATH = path.join(
  process.cwd(),
  "test-results",
  "shell-1440x900-header.png",
);

test("shell-area-pill renders next to the brand in the single header", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1440, height: 900 });

  await page.route("**/api/v1/nav-counts", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      json: {
        analytics: 0,
        decisions: 0,
        graph: 0,
        learnings: 0,
        pipelines: 0,
        settingsTokens: 0,
        work: 0,
      },
      status: 200,
    });
  });
  await page.route("**/api/health", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      json: { status: "ok", deps: { api: { status: "ok" } } },
      status: 200,
    });
  });

  await openAuthedView(page, "/pipelines");

  const header = page.locator('[class~="aq-header"]');
  await expect(header).toHaveCount(1);

  await expect(header.locator('[class~="aq-brand"]')).toBeVisible();
  await expect(header.locator('[class~="aq-area"]')).toBeVisible();

  const areaFollowsBrand = await header.evaluate((element) => {
    const brand = element.querySelector('[class~="aq-brand"]');
    const area = element.querySelector('[class~="aq-area"]');

    if (!brand || !area || brand.parentElement !== area.parentElement) {
      return false;
    }

    return Boolean(
      brand.compareDocumentPosition(area) & Node.DOCUMENT_POSITION_FOLLOWING,
    );
  });

  expect(areaFollowsBrand).toBe(true);

  await header.screenshot({ path: HEADER_SCREENSHOT_PATH });
});
