import { expect, test } from "@playwright/test";

import { seedAuthenticatedSession } from "./helpers";

const E2E_STATE_URL = "http://127.0.0.1:3127/__aq_e2e/state";

test("second pipelines navigation reuses the cached aggregate", async ({
  page,
  request,
}) => {
  await request.post(E2E_STATE_URL, {
    data: {
      needs_bootstrap: false,
      pipelines_delay_ms: 120,
    },
  });

  try {
    await seedAuthenticatedSession(page);

    await page.goto("/pipelines");
    await expect(
      page.getByRole("button", { name: /Realtime ingestion rebuild/ }),
    ).toBeVisible();

    await page.getByRole("link", { name: "Settings" }).click();
    await expect(
      page.getByRole("heading", { level: 1, name: "Settings" }),
    ).toBeVisible();

    const startedAt = Date.now();
    await page.getByRole("link", { name: "Pipelines" }).click();
    await expect(
      page.getByRole("button", { name: /Realtime ingestion rebuild/ }),
    ).toBeVisible();
    const secondNavigationMs = Date.now() - startedAt;

    expect(secondNavigationMs).toBeLessThan(300);
  } finally {
    await request.post(E2E_STATE_URL, {
      data: {
        needs_bootstrap: false,
        pipelines_delay_ms: 0,
      },
    });
  }
});
