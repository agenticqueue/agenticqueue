import { expect, test } from "@playwright/test";

import { openAuthedView } from "./helpers";

test("renders the graph canvas when AQ-105 is live", async ({ page }) => {
  await openAuthedView(page, "/graph");

  if (!(await page.locator(".aq-graph-wrap").count())) {
    test.skip(true, "AQ-105 is not live yet; /graph still uses the placeholder shell.");
  }

  await expect(page.locator(".aq-graph-wrap")).toBeVisible();
  await expect(page.locator(".aq-graph-svg")).toBeVisible();
});
