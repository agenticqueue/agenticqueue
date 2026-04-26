import { expect, test } from "@playwright/test";

import { openAuthedView } from "./helpers";

const WORK_FIXTURE = {
  generated_at: "2026-04-21T15:04:45.000Z",
  count: 6,
  items: [
    {
      id: "task-104",
      ref: "job-104",
      title: "Work view — cross-pipeline job table",
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
      outputs: [],
      decisions: [],
      activity: [],
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
      activity: [],
      parent_ref: null,
      dependency_refs: [],
      blocked_by_refs: [],
      block_refs: [],
      child_refs: [],
    },
    {
      id: "task-201",
      ref: "job-201",
      title: "Failed packet replay",
      pipeline: "Runtime Hardening",
      pipeline_slug: "runtime-hardening",
      actor: "claude",
      claimed_at: "2026-04-21T13:30:00.000Z",
      closed_at: null,
      created_at: "2026-04-21T13:00:00.000Z",
      updated_at: "2026-04-21T14:45:00.000Z",
      status: "failed",
      raw_state: "failed",
      priority: 3,
      task_type: "coding-task",
      description: "Replay a failed packet and capture diagnostics.",
      labels: ["needs:coding"],
      outputs: [],
      decisions: [],
      activity: [],
      parent_ref: null,
      dependency_refs: [],
      blocked_by_refs: [],
      block_refs: [],
      child_refs: [],
    },
    {
      id: "task-202",
      ref: "job-202",
      title: "Review queue dashboard",
      pipeline: "Runtime Hardening",
      pipeline_slug: "runtime-hardening",
      actor: "gemini",
      claimed_at: "2026-04-21T12:30:00.000Z",
      closed_at: null,
      created_at: "2026-04-21T12:20:00.000Z",
      updated_at: "2026-04-21T14:20:00.000Z",
      status: "review",
      raw_state: "validated",
      priority: 2,
      task_type: "review-task",
      description: "Waiting for human sign-off.",
      labels: ["needs:review"],
      outputs: [],
      decisions: [],
      activity: [],
      parent_ref: null,
      dependency_refs: ["job-201"],
      blocked_by_refs: [],
      block_refs: [],
      child_refs: [],
    },
    {
      id: "task-301",
      ref: "job-301",
      title: "Release note packet",
      pipeline: "Launch Readiness",
      pipeline_slug: "launch-readiness",
      actor: "codex",
      claimed_at: "2026-04-21T10:35:00.000Z",
      closed_at: "2026-04-21T11:30:00.000Z",
      created_at: "2026-04-21T10:31:00.000Z",
      updated_at: "2026-04-21T11:30:00.000Z",
      status: "done",
      raw_state: "done",
      priority: 1,
      task_type: "coding-task",
      description: "Publish the release packet after docs lock.",
      labels: ["phase:7"],
      outputs: [],
      decisions: [],
      activity: [],
      parent_ref: "job-202",
      dependency_refs: [],
      blocked_by_refs: [],
      block_refs: [],
      child_refs: [],
    },
    {
      id: "task-401",
      ref: "job-401",
      title: "Blocked release gate",
      pipeline: "Launch Readiness",
      pipeline_slug: "launch-readiness",
      actor: null,
      claimed_at: null,
      closed_at: null,
      created_at: "2026-04-21T09:31:00.000Z",
      updated_at: "2026-04-21T09:40:00.000Z",
      status: "blocked",
      raw_state: "blocked",
      priority: 1,
      task_type: "coding-task",
      description: "Blocked by upstream verification.",
      labels: ["blocked"],
      outputs: [],
      decisions: [],
      activity: [],
      parent_ref: null,
      dependency_refs: [],
      blocked_by_refs: ["job-301"],
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
      page.locator(`.aq-tab.is-active[href="${route.href}"]`),
    ).toHaveCount(1);
    await expect(
      page.getByRole("link", { name: /^Settings$/i }),
    ).toBeVisible();
  }
});

test("work-subtabs renders six status tabs with counts", async ({ page }) => {
  await openAuthedView(page, "/work", {
    workPayload: WORK_FIXTURE,
  });

  await expect(page.locator(".aq-subtab")).toHaveCount(6);
  await expect(page.getByTestId("work-subtab-all")).toContainText("6");
  await expect(page.getByTestId("work-subtab-running")).toContainText("1");
  await expect(page.getByTestId("work-subtab-failed")).toContainText("1");
  await expect(page.getByTestId("work-subtab-review")).toContainText("1");
  await expect(page.getByTestId("work-subtab-queued")).toContainText("1");
  await expect(page.getByTestId("work-subtab-done")).toContainText("1");

  await page.getByTestId("work-subtab-queued").click();

  await expect(page.getByTestId("work-row-job-115")).toBeVisible();
  await expect(page.getByTestId("work-row-job-104")).toHaveCount(0);
});

test("work-side-panel opens shared job detail panel", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await openAuthedView(page, "/work", {
    workPayload: WORK_FIXTURE,
  });

  await page.getByTestId("work-row-job-104").click();

  const panel = page.locator(".aq-side-panel .aq-job-detail");
  await expect(panel).toBeVisible();
  await expect(panel).toContainText("Work view — cross-pipeline job table");
  await expect(panel).toContainText("job-104");
  await expect(panel.getByRole("button", { name: "Close job detail" })).toBeVisible();

  await page.addStyleTag({
    content: "nextjs-portal { display: none !important; }",
  });
  await page.screenshot({
    path: "test-results/work-1440x900-subtabs-panel.png",
  });
});

test("work-keyboard-nav moves through filtered rows and closes panel", async ({
  page,
}) => {
  await openAuthedView(page, "/work", {
    workPayload: WORK_FIXTURE,
  });

  await expect(page.getByTestId("work-row-job-104")).toBeVisible();
  await page.keyboard.press("ArrowDown");
  await expect(page.locator(".aq-job-detail")).toContainText("job-104");

  await page.keyboard.press("ArrowDown");
  await expect(page.locator(".aq-job-detail")).toContainText("job-115");

  await page.keyboard.press("ArrowUp");
  await expect(page.locator(".aq-job-detail")).toContainText("job-104");

  await page.keyboard.press("Escape");
  await expect(page.locator(".aq-side-panel")).toHaveCount(0);

  await page.getByTestId("work-subtab-queued").click();
  await expect(page.getByTestId("work-row-job-115")).toBeVisible();
  await page.keyboard.press("ArrowDown");
  await expect(page.locator(".aq-job-detail")).toContainText("job-115");

  await page.keyboard.press("Escape");
  await expect(page.locator(".aq-side-panel")).toHaveCount(0);
});
