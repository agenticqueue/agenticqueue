import { expect, test } from "@playwright/test";

import {
  expectNoStoredSessionSecrets,
  mockShellReadApis,
  openAuthedView,
  seedAuthenticatedSession,
} from "./helpers";

const WORK_FIXTURE = {
  generated_at: "2026-04-21T15:04:45.000Z",
  count: 2,
  items: [
    {
      id: "task-104",
      ref: "job-104",
      title: "Work view — cross-pipeline Job table + right-side detail panel",
      pipeline: "Phase 7 - Web UI",
      pipeline_slug: "phase-7-web-ui",
      actor: "codex",
      claimed_at: "2026-04-21T15:00:00.000Z",
      closed_at: null,
      created_at: "2026-04-21T14:00:00.000Z",
      updated_at: "2026-04-21T15:03:00.000Z",
      status: "running",
      raw_state: "in_progress",
      priority: 5,
      task_type: "coding-task",
      description: "Build the Phase 7 work queue lens on top of the shell scaffold.",
      labels: ["phase:7", "needs:coding", "agent:codex"],
      outputs: [
        {
          id: "artifact-104",
          kind: "diff",
          label: "work-view.patch",
          uri: "file:///tmp/work-view.patch",
          created_at: "2026-04-21T15:02:00.000Z",
          run_ref: "run-abcd1234",
        },
      ],
      decisions: [
        {
          id: "decision-104",
          summary: "Keep the Work panel read-only",
          rationale: "Product policy keeps mutations outside the UI.",
          decided_at: "2026-04-21T15:01:00.000Z",
          actor: "codex",
          run_ref: "run-abcd1234",
        },
      ],
      activity: [
        {
          id: "activity-104-1",
          label: "queued -> in_progress",
          summary: "Task transitioned into active execution.",
          happened_at: "2026-04-21T15:00:00.000Z",
          state: "running",
          source: "run",
          command: "aq claim job-104",
        },
        {
          id: "activity-104-2",
          label: "Decision recorded",
          summary: "Keep the Work panel read-only",
          happened_at: "2026-04-21T15:01:00.000Z",
          state: null,
          source: "decision",
          command: null,
        },
      ],
      parent_ref: null,
      dependency_refs: ["job-102"],
      blocked_by_refs: [],
      block_refs: ["job-115"],
      child_refs: [],
    },
    {
      id: "task-115",
      ref: "job-115",
      title: "Playwright smoke tests — 5-view critical path",
      pipeline: "Phase 7 - Web UI",
      pipeline_slug: "phase-7-web-ui",
      actor: null,
      claimed_at: null,
      closed_at: null,
      created_at: "2026-04-21T14:10:00.000Z",
      updated_at: "2026-04-21T14:59:00.000Z",
      status: "queued",
      raw_state: "queued",
      priority: 4,
      task_type: "coding-task",
      description: "Cover the five core read-only views with smoke tests.",
      labels: ["phase:7", "needs:coding"],
      outputs: [],
      decisions: [],
      activity: [
        {
          id: "activity-115-1",
          label: "Task created",
          summary: "Smoke coverage is ready once the work view lands.",
          happened_at: "2026-04-21T14:10:00.000Z",
          state: "queued",
          source: "task",
          command: null,
        },
      ],
      parent_ref: null,
      dependency_refs: [],
      blocked_by_refs: [],
      block_refs: [],
      child_refs: [],
    },
  ],
};

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

test("returns expired sessions to the login shell", async ({ page }) => {
  await seedAuthenticatedSession(page, {
    sessionJson: { error: "Session expired." },
    sessionStatus: 401,
  });
  await mockShellReadApis(page);

  await page.goto("/pipelines");

  await expect(
    page.getByRole("heading", {
      level: 1,
      name: "Sign in to AgenticQueue",
    }),
  ).toBeVisible();

  const stored = await expectNoStoredSessionSecrets(page);
  expect(stored.localKeys).toEqual([]);
  expect(stored.sessionKeys).toEqual([]);
});

test("renders the work queue detail panel when AQ-104 is live", async ({
  page,
}) => {
  await openAuthedView(page, "/work", {
    workPayload: WORK_FIXTURE,
  });

  await expect(page.locator(".aq-table-work")).toBeVisible();
  await expect(page.getByTestId("work-row-job-104")).toBeVisible();

  const firstRow = page.getByTestId("work-row-job-104");
  await expect(firstRow).toBeVisible();
  const started = await page.evaluate(() => performance.now());
  await firstRow.click();
  const finished = await page.evaluate(() => performance.now());

  const detail = page.getByTestId("work-detail");
  await expect(detail).toBeVisible();
  await expect(detail).toContainText("job-104");
  await expect(finished - started).toBeLessThan(100);
  await detail.getByRole("tab", { name: "Outputs" }).click();
  await expect(detail).toContainText("work-view.patch");
  await detail.getByRole("tab", { name: "Activity log" }).click();
  await expect(detail).toContainText("aq claim job-104");
  await detail.getByRole("tab", { name: "Properties" }).click();
  await expect(detail).toContainText("job-115");
  await expect(
    detail.getByRole("button", { name: /approve/i }),
  ).toHaveCount(0);
  await expect(
    detail.getByRole("button", { name: /reject/i }),
  ).toHaveCount(0);

  await page.keyboard.press("Shift+/");
  await expect(page.getByTestId("work-shortcuts")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("work-shortcuts")).toHaveCount(0);

  await page.keyboard.press("ArrowDown");
  await expect(detail).toContainText("job-115");
});
