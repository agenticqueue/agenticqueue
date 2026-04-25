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
  count: 2,
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
          blocks: [{ id: "task-102", ref: "job-102", title: "Execution packet compiler", status: "running", raw_state: "in_progress" }],
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
          depends_on: [{ id: "task-101", ref: "job-101", title: "Contract pass on ingest worker", status: "done", raw_state: "done" }],
          blocked_by: [],
          blocks: [{ id: "task-103", ref: "job-103", title: "Read-only surface verification", status: "queued", raw_state: "queued" }],
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
          depends_on: [{ id: "task-102", ref: "job-102", title: "Execution packet compiler", status: "running", raw_state: "in_progress" }],
          blocked_by: [{ id: "task-102", ref: "job-102", title: "Execution packet compiler", status: "running", raw_state: "in_progress" }],
          blocks: [],
        },
      ],
    },
    {
      id: "pipeline-2",
      slug: "graph-hardening",
      name: "Execution graph hardening",
      goal: "Bring relation visualizations into the logged-in shell without adding mutation affordances.",
      state: "in_progress",
      tone: "warn",
      progress: { done: 0, total: 2 },
      autonomy: { label: "Autonomy tier 2", tone: "ok" },
      attention: {
        failed: 0,
        needs_review: 1,
        running: 0,
        queued: 0,
        blocked: 1,
      },
      started_at: "2026-04-21T12:20:00.000Z",
      updated_at: "2026-04-21T12:59:00.000Z",
      completed_at: null,
      tasks: [
        {
          id: "task-201",
          ref: "job-201",
          title: "Dependency lane renderer",
          task_type: "coding-task",
          status: "review",
          raw_state: "validated",
          priority: 2,
          labels: ["phase:7"],
          sequence: 201,
          description: "Draft the dependency lane so reviewers can inspect pipeline state at a glance.",
          claimed_by_actor_id: "actor-2",
          claimed_at: "2026-04-21T12:25:00.000Z",
          created_at: "2026-04-21T12:20:00.000Z",
          updated_at: "2026-04-21T12:58:00.000Z",
          parent_ref: null,
          dependency_refs: [],
          child_refs: ["job-202"],
          depends_on: [],
          blocked_by: [],
          blocks: [{ id: "task-202", ref: "job-202", title: "Review queue dashboard", status: "blocked", raw_state: "blocked" }],
        },
        {
          id: "task-202",
          ref: "job-202",
          title: "Review queue dashboard",
          task_type: "coding-task",
          status: "blocked",
          raw_state: "blocked",
          priority: 1,
          labels: ["phase:7", "blocked"],
          sequence: 202,
          description: "Stay blocked until the dependency lane lands.",
          claimed_by_actor_id: null,
          claimed_at: null,
          created_at: "2026-04-21T12:29:00.000Z",
          updated_at: "2026-04-21T12:59:00.000Z",
          parent_ref: "job-201",
          dependency_refs: ["job-201"],
          child_refs: [],
          depends_on: [],
          blocked_by: [{ id: "task-201", ref: "job-201", title: "Dependency lane renderer", status: "review", raw_state: "validated" }],
          blocks: [],
        },
      ],
    },
  ],
};

const donePayload = {
  state: "done",
  count: 1,
  generated_at: "2026-04-21T13:03:20.069Z",
  pipelines: [
    {
      id: "pipeline-3",
      slug: "launch-readiness",
      name: "Customer launch prep",
      goal: "Keep the launch checklist visible in the same shell once contracts are fully complete.",
      state: "done",
      tone: "ok",
      progress: { done: 2, total: 2 },
      autonomy: { label: "Autonomy tier 1", tone: "ok" },
      attention: {
        failed: 0,
        needs_review: 0,
        running: 0,
        queued: 0,
        blocked: 0,
      },
      started_at: "2026-04-20T18:00:00.000Z",
      updated_at: "2026-04-21T11:30:00.000Z",
      completed_at: "2026-04-21T11:30:00.000Z",
      tasks: [
        {
          id: "task-301",
          ref: "job-301",
          title: "Docs readiness pass",
          task_type: "coding-task",
          status: "done",
          raw_state: "done",
          priority: 1,
          labels: ["phase:7"],
          sequence: 301,
          description: "Make the final docs pass visible from the launch pipeline.",
          claimed_by_actor_id: "actor-1",
          claimed_at: "2026-04-21T10:00:00.000Z",
          created_at: "2026-04-21T09:00:00.000Z",
          updated_at: "2026-04-21T10:30:00.000Z",
          parent_ref: null,
          dependency_refs: [],
          child_refs: ["job-302"],
          depends_on: [],
          blocked_by: [],
          blocks: [{ id: "task-302", ref: "job-302", title: "Release note packet", status: "done", raw_state: "done" }],
        },
        {
          id: "task-302",
          ref: "job-302",
          title: "Release note packet",
          task_type: "coding-task",
          status: "done",
          raw_state: "done",
          priority: 1,
          labels: ["phase:7"],
          sequence: 302,
          description: "Publish the release packet after docs lock.",
          claimed_by_actor_id: "actor-1",
          claimed_at: "2026-04-21T10:35:00.000Z",
          created_at: "2026-04-21T10:31:00.000Z",
          updated_at: "2026-04-21T11:30:00.000Z",
          parent_ref: "job-301",
          dependency_refs: ["job-301"],
          child_refs: [],
          depends_on: [{ id: "task-301", ref: "job-301", title: "Docs readiness pass", status: "done", raw_state: "done" }],
          blocked_by: [],
          blocks: [],
        },
      ],
    },
  ],
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

test("renders pipeline sections and expands the inline DAG", async ({ page }) => {
  await page.goto("/pipelines");

  await expect(
    page.getByRole("heading", { level: 1, name: "Pipelines" }),
  ).toBeVisible();
  await expect(
    page.getByText("Writes stay outside the web shell. Claim, submit, approve, reject, and mutate through the API, CLI, or MCP."),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: /Pipelines \(in progress\)/ }),
  ).toContainText("2");
  await expect(
    page.getByRole("button", { name: /Realtime ingestion rebuild/ }),
  ).toBeVisible();
  await expect(page.getByText("execution chain inline · read-only")).toBeVisible();

  await page.getByRole("button", { name: /Execution graph hardening/ }).click();
  const dagCard = page.locator(".aq-jobcard").filter({ hasText: "job-202" });
  await expect(dagCard).toBeVisible();
  await dagCard.click();
  const jobDetail = page.locator(".aq-job-detail").filter({
    hasText: "Review queue dashboard",
  });
  await expect(jobDetail).toBeVisible();
  await expect(
    jobDetail.getByText(
      "Writes are disabled here. Use `aq` or MCP for task actions.",
    ),
  ).toBeVisible();

  await page.getByRole("button", { name: /Pipelines \(completed\)/ }).click();
  await expect(
    page.getByRole("button", { name: /Customer launch prep/ }),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: /approve/i })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /reject/i })).toHaveCount(0);
});
