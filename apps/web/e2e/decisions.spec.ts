import { expect, test } from "@playwright/test";

import { openAuthedView } from "./helpers";

test("renders the decisions ledger detail panel when AQ-183 is live", async ({
  page,
}) => {
  await openAuthedView(page, "/decisions");

  if (!(await page.locator(".aq-decision-list").count())) {
    test.skip(true, "AQ-183 is not live yet; /decisions still uses the placeholder shell.");
  }

  await expect(page.locator(".aq-decision-list")).toBeVisible();

  const approvedTab = page.getByRole("button", { name: /Approved/i });
  await expect(approvedTab).toBeVisible();
  await approvedTab.click();

  const firstRow = page.locator(".aq-decision-row").first();
  await expect(firstRow).toBeVisible();
  await firstRow.click();

  const detail = page.locator(".aq-detail");
  await expect(detail).toBeVisible();
  await expect(detail.getByText("Rationale")).toBeVisible();
});
