import { expect, test } from "@playwright/test";

import { seedAuthenticatedSession } from "./helpers";

const delayedInProgressPayload = {
  state: "in_progress",
  count: 1,
  generated_at: "2026-04-25T07:31:00.000Z",
  pipelines: [
    {
      id: "pipeline-1",
      slug: "ingestion-core",
      name: "Realtime ingestion rebuild",
      goal: "Stabilize the ingest chain before the broader execution rollout.",
      state: "in_progress",
      tone: "info",
      progress: { done: 1, total: 3 },
      autonomy: { label: "HITL required · tier 3", tone: "warn" },
      attention: {
        failed: 0,
        needs_review: 0,
        running: 1,
        queued: 1,
        blocked: 0,
      },
      started_at: "2026-04-21T12:45:00.000Z",
      updated_at: "2026-04-21T13:00:00.000Z",
      completed_at: null,
      tasks: [],
    },
  ],
};

const emptyDonePayload = {
  state: "done",
  count: 0,
  generated_at: "2026-04-25T07:31:00.000Z",
  pipelines: [],
};

test("shows the loader at 500ms and paints the pipeline list after a 2s delay", async ({
  page,
}) => {
  await seedAuthenticatedSession(page);

  await page.route("**/api/v1/pipelines?state=in_progress", async (route) => {
    await page.waitForTimeout(2_000);
    await route.fulfill({
      contentType: "application/json",
      json: delayedInProgressPayload,
      status: 200,
    });
  });

  await page.route("**/api/v1/pipelines?state=done", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      json: emptyDonePayload,
      status: 200,
    });
  });

  await page.goto("/pipelines");

  await page.waitForTimeout(500);
  await expect(page.locator(".aq-state-loader")).toBeVisible();
  await expect(page.locator(".aq-pipeline-skeleton-row")).toHaveCount(3);
  await expect(
    page.getByRole("button", { name: /Realtime ingestion rebuild/ }),
  ).toHaveCount(0);

  await expect(
    page.getByRole("button", { name: /Realtime ingestion rebuild/ }),
  ).toBeVisible({ timeout: 3_000 });
  await expect(page.locator(".aq-state-loader")).toHaveCount(0);
});
