import path from "node:path";

import { expect, test } from "@playwright/test";

const sessionPayload = {
  actor: {
    id: "actor-1",
    handle: "codex-hourly",
    actor_type: "admin",
    display_name: "Codex Runner",
  },
  tokenCount: 1,
  apiBaseUrl: "http://127.0.0.1:8010",
};

const E2E_WEB_BASE_URL = `http://127.0.0.1:${process.env.AQ_E2E_WEB_PORT ?? "3005"}`;

const inProgressPayload = {
  state: "in_progress",
  count: 1,
  generated_at: "2026-04-21T13:03:20.069Z",
  pipelines: [
    {
      id: "pipeline-1",
      slug: "ingestion-core",
      name: "Realtime ingestion rebuild",
      goal: "Stabilize the ingest chain before the broader execution rollout.",
      state: "in_progress",
      tone: "info",
      progress: { done: 1, total: 3 },
      autonomy: { label: "HITL required - tier 3", tone: "warn" },
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
      tasks: [
        {
          id: "task-101",
          ref: "job-101",
          title: "Contract pass on ingest worker",
          task_type: "coding-task",
          status: "done",
          raw_state: "done",
          priority: 2,
          labels: ["phase:7"],
          sequence: 101,
          description: "Finish the worker contract and publish the packet.",
          claimed_by_actor_id: "actor-1",
          claimed_at: "2026-04-21T12:46:00.000Z",
          created_at: "2026-04-21T12:40:00.000Z",
          updated_at: "2026-04-21T12:50:00.000Z",
          parent_ref: null,
          dependency_refs: [],
          child_refs: ["job-102"],
          depends_on: [],
          blocked_by: [],
          blocks: [
            {
              id: "task-102",
              ref: "job-102",
              title: "Execution packet compiler",
              status: "running",
              raw_state: "in_progress",
            },
          ],
        },
        {
          id: "task-102",
          ref: "job-102",
          title: "Execution packet compiler",
          task_type: "coding-task",
          status: "running",
          raw_state: "in_progress",
          priority: 2,
          labels: ["phase:7", "needs:coding"],
          sequence: 102,
          description: "Compile the packet and keep the shape stable for DAG consumers.",
          claimed_by_actor_id: "actor-1",
          claimed_at: "2026-04-21T12:52:00.000Z",
          created_at: "2026-04-21T12:48:00.000Z",
          updated_at: "2026-04-21T13:00:00.000Z",
          parent_ref: "job-101",
          dependency_refs: ["job-101"],
          child_refs: ["job-103"],
          depends_on: [
            {
              id: "task-101",
              ref: "job-101",
              title: "Contract pass on ingest worker",
              status: "done",
              raw_state: "done",
            },
          ],
          blocked_by: [],
          blocks: [
            {
              id: "task-103",
              ref: "job-103",
              title: "Read-only surface verification",
              status: "queued",
              raw_state: "queued",
            },
          ],
        },
        {
          id: "task-103",
          ref: "job-103",
          title: "Read-only surface verification",
          task_type: "coding-task",
          status: "queued",
          raw_state: "queued",
          priority: 1,
          labels: ["phase:7", "needs:review"],
          sequence: 103,
          description: "Verify the final browser shell against the preview contract.",
          claimed_by_actor_id: null,
          claimed_at: null,
          created_at: "2026-04-21T12:55:00.000Z",
          updated_at: "2026-04-21T12:55:00.000Z",
          parent_ref: "job-102",
          dependency_refs: ["job-101", "job-102"],
          child_refs: [],
          depends_on: [
            {
              id: "task-102",
              ref: "job-102",
              title: "Execution packet compiler",
              status: "running",
              raw_state: "in_progress",
            },
          ],
          blocked_by: [
            {
              id: "task-102",
              ref: "job-102",
              title: "Execution packet compiler",
              status: "running",
              raw_state: "in_progress",
            },
          ],
          blocks: [],
        },
      ],
    },
  ],
};

const donePayload = {
  state: "done",
  count: 0,
  generated_at: "2026-04-21T13:03:20.069Z",
  pipelines: [],
};

test.beforeEach(async ({ page }) => {
  await page.context().addCookies([
    {
      name: "aq_session",
      value: "playwright",
      url: E2E_WEB_BASE_URL,
    },
  ]);

  await page.addInitScript(() => {
    window.localStorage.setItem("aq:web:remember-token", "false");
    window.sessionStorage.setItem("aq:web:api-token", "aq_live_playwright_token");
  });

  await page.route("**/api/session", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      json: sessionPayload,
      status: 200,
    });
  });

  await page.route("**/api/v1/nav-counts", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      json: {
        analytics: 0,
        decisions: 0,
        graph: 0,
        learnings: 0,
        pipelines: 1,
        settingsTokens: 0,
        work: 3,
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

  await page.route("**/api/v1/pipelines**", async (route) => {
    const state = new URL(route.request().url()).searchParams.get("state");
    const payload = state === "done" ? donePayload : inProgressPayload;
    await route.fulfill({
      contentType: "application/json",
      json: payload,
      status: 200,
    });
  });
});

test("pipelines-side-panel-open: click job opens the right-edge detail panel", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/pipelines");

  const jobCard = page.locator(".aq-jobcard").filter({ hasText: "job-102" });
  await jobCard.click();

  const panel = page.locator(".aq-side-panel.is-open");
  await expect(panel).toBeVisible();
  await expect(panel.locator(".aq-job-detail")).toContainText(
    "Execution packet compiler",
  );

  await page.addStyleTag({
    content: "nextjs-portal { display: none !important; }",
  });
  await page.screenshot({
    fullPage: true,
    path: path.join(
      process.cwd(),
      "test-results",
      "pipelines-1440x900-side-panel.png",
    ),
  });
});

test("pipelines-side-panel-keyboard: ArrowRight selects the next job", async ({
  page,
}) => {
  await page.goto("/pipelines");
  await page.locator(".aq-jobcard").filter({ hasText: "job-101" }).click();

  await page.keyboard.press("ArrowRight");

  await expect(page.locator(".aq-side-panel.is-open")).toContainText(
    "Execution packet compiler",
  );
  await expect(
    page.locator(".aq-jobcard.is-selected").filter({ hasText: "job-102" }),
  ).toBeVisible();
});

test("pipelines-side-panel-keyboard: ArrowLeft selects the previous job", async ({
  page,
}) => {
  await page.goto("/pipelines");
  await page.locator(".aq-jobcard").filter({ hasText: "job-102" }).click();

  await page.keyboard.press("ArrowLeft");

  await expect(page.locator(".aq-side-panel.is-open")).toContainText(
    "Contract pass on ingest worker",
  );
  await expect(
    page.locator(".aq-jobcard.is-selected").filter({ hasText: "job-101" }),
  ).toBeVisible();
});

test("pipelines-side-panel-keyboard: arrow navigation loops at the edges", async ({
  page,
}) => {
  await page.goto("/pipelines");
  await page.locator(".aq-jobcard").filter({ hasText: "job-103" }).click();

  await page.keyboard.press("ArrowRight");
  await expect(page.locator(".aq-side-panel.is-open")).toContainText(
    "Contract pass on ingest worker",
  );

  await page.keyboard.press("ArrowLeft");
  await expect(page.locator(".aq-side-panel.is-open")).toContainText(
    "Read-only surface verification",
  );
});

test("pipelines-side-panel-escape: Escape closes the panel and returns focus", async ({
  page,
}) => {
  await page.goto("/pipelines");
  const jobCard = page.locator(".aq-jobcard").filter({ hasText: "job-102" });
  await jobCard.click();
  await expect(page.locator(".aq-side-panel.is-open")).toBeVisible();

  await page.keyboard.press("Escape");

  await expect(page.locator(".aq-side-panel")).toHaveCount(0);
  await expect(jobCard).toBeFocused();
});
