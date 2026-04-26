import { expect, test } from "@playwright/test";

import { openAuthedView } from "./helpers";

const DECISIONS_FIXTURE = {
  generated_at: "2026-04-21T15:24:19.000Z",
  count: 4,
  items: [
    {
      id: "decision-current",
      ref: "dc-current",
      title: "Use the read-only Decisions ledger in the Phase 7 shell",
      scope: "project",
      actor: "codex",
      decided_at: "2026-04-21T15:00:00.000Z",
      status: "active",
      rationale:
        "Decisions must stay visible in the shell while all mutations remain in CLI, REST, and MCP surfaces.",
      project_name: "Phase 7 - Web UI",
      project_slug: "phase-7-web-ui",
      primary_job_ref: "job-104",
      linked_job_refs: ["job-104", "job-183"],
      supersedes_refs: ["dc-prev"],
      superseded_by_refs: [],
      alternative_refs: [
        {
          id: "decision-alt",
          ref: "dc-alt",
          title: "Keep governance hidden in docs only",
        },
      ],
    },
    {
      id: "decision-prev",
      ref: "dc-prev",
      title: "Prototype decisions as a lightweight panel",
      scope: "project",
      actor: "claude",
      decided_at: "2026-04-20T16:00:00.000Z",
      status: "superseded",
      rationale:
        "The panel de-risked the nav position, but the table contract needed stronger scope and lineage semantics.",
      project_name: "Phase 7 - Web UI",
      project_slug: "phase-7-web-ui",
      primary_job_ref: "job-183",
      linked_job_refs: ["job-183"],
      supersedes_refs: ["dc-root"],
      superseded_by_refs: ["dc-current"],
      alternative_refs: [],
    },
    {
      id: "decision-root",
      ref: "dc-root",
      title: "Track governance decisions directly in the product shell",
      scope: "project",
      actor: "ghost",
      decided_at: "2026-04-19T12:00:00.000Z",
      status: "superseded",
      rationale:
        "If humans cannot inspect decisions in the product surface, the shell fails the UI-always-on requirement.",
      project_name: "Phase 7 - Web UI",
      project_slug: "phase-7-web-ui",
      primary_job_ref: "job-102",
      linked_job_refs: ["job-102"],
      supersedes_refs: [],
      superseded_by_refs: ["dc-prev"],
      alternative_refs: [],
    },
    {
      id: "decision-task",
      ref: "dc-task",
      title: "Narrow AQ-183 smoke coverage to the decisions route only",
      scope: "task",
      actor: "codex",
      decided_at: "2026-04-21T14:30:00.000Z",
      status: "active",
      rationale: "Task-local decision used to verify scope filtering.",
      project_name: "Phase 7 - Web UI",
      project_slug: "phase-7-web-ui",
      primary_job_ref: "job-183",
      linked_job_refs: ["job-183"],
      supersedes_refs: [],
      superseded_by_refs: [],
      alternative_refs: [],
    },
  ],
};

const LINEAGE_FIXTURES = {
  "decision-current": {
    generated_at: "2026-04-21T15:24:19.000Z",
    decision_id: "decision-current",
    nodes: [
      {
        id: "decision-current",
        ref: "dc-current",
        title: "Use the read-only Decisions ledger in the Phase 7 shell",
        decided_at: "2026-04-21T15:00:00.000Z",
        status: "active",
        scope: "project",
        relation: "selected",
        depth: 0,
      },
      {
        id: "decision-prev",
        ref: "dc-prev",
        title: "Prototype decisions as a lightweight panel",
        decided_at: "2026-04-20T16:00:00.000Z",
        status: "superseded",
        scope: "project",
        relation: "older",
        depth: 1,
      },
      {
        id: "decision-root",
        ref: "dc-root",
        title: "Track governance decisions directly in the product shell",
        decided_at: "2026-04-19T12:00:00.000Z",
        status: "superseded",
        scope: "project",
        relation: "older",
        depth: 2,
      },
    ],
    edges: [
      {
        from_id: "decision-current",
        to_id: "decision-prev",
        from_ref: "dc-current",
        to_ref: "dc-prev",
      },
      {
        from_id: "decision-prev",
        to_id: "decision-root",
        from_ref: "dc-prev",
        to_ref: "dc-root",
      },
    ],
  },
  "decision-prev": {
    generated_at: "2026-04-21T15:24:19.000Z",
    decision_id: "decision-prev",
    nodes: [
      {
        id: "decision-current",
        ref: "dc-current",
        title: "Use the read-only Decisions ledger in the Phase 7 shell",
        decided_at: "2026-04-21T15:00:00.000Z",
        status: "active",
        scope: "project",
        relation: "newer",
        depth: -1,
      },
      {
        id: "decision-prev",
        ref: "dc-prev",
        title: "Prototype decisions as a lightweight panel",
        decided_at: "2026-04-20T16:00:00.000Z",
        status: "superseded",
        scope: "project",
        relation: "selected",
        depth: 0,
      },
      {
        id: "decision-root",
        ref: "dc-root",
        title: "Track governance decisions directly in the product shell",
        decided_at: "2026-04-19T12:00:00.000Z",
        status: "superseded",
        scope: "project",
        relation: "older",
        depth: 1,
      },
    ],
    edges: [
      {
        from_id: "decision-current",
        to_id: "decision-prev",
        from_ref: "dc-current",
        to_ref: "dc-prev",
      },
      {
        from_id: "decision-prev",
        to_id: "decision-root",
        from_ref: "dc-prev",
        to_ref: "dc-root",
      },
    ],
  },
};

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
      outputs: [],
      decisions: [],
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
      ],
      parent_ref: null,
      dependency_refs: [],
      blocked_by_refs: [],
      block_refs: [],
      child_refs: [],
    },
    {
      id: "task-183",
      ref: "job-183",
      title: "Decisions view — read-only list + detail panel",
      pipeline: "Phase 7 - Web UI",
      pipeline_slug: "phase-7-web-ui",
      actor: "codex",
      claimed_at: "2026-04-21T15:10:00.000Z",
      closed_at: null,
      created_at: "2026-04-21T14:20:00.000Z",
      updated_at: "2026-04-21T15:20:00.000Z",
      status: "running",
      raw_state: "in_progress",
      priority: 5,
      task_type: "coding-task",
      description: "Build the AQ-183 decisions ledger view on top of the shared shell.",
      labels: ["phase:7", "needs:coding", "agent:codex"],
      outputs: [],
      decisions: [],
      activity: [
        {
          id: "activity-183-1",
          label: "queued -> in_progress",
          summary: "Task transitioned into active execution.",
          happened_at: "2026-04-21T15:10:00.000Z",
          state: "running",
          source: "run",
          command: "aq claim job-183",
        },
      ],
      parent_ref: null,
      dependency_refs: ["job-104"],
      blocked_by_refs: [],
      block_refs: [],
      child_refs: [],
    },
  ],
};

test("renders the AQ-183 decisions ledger, lineage, and linked-job navigation", async ({
  page,
}) => {
  await openAuthedView(page, "/decisions", {
    decisionsPayload: DECISIONS_FIXTURE,
    decisionLineageById: LINEAGE_FIXTURES,
    workPayload: WORK_FIXTURE,
  });

  await expect(page.locator(".aq-knowledge-list")).toBeVisible();

  const projectScope = page
    .locator(".aq-filter-group")
    .filter({ hasText: "Scope" })
    .getByRole("button", { name: /Project/i });
  await expect(projectScope).toBeVisible();
  await projectScope.click();

  await expect(page.getByTestId("decision-row-dc-current")).toBeVisible();
  await expect(page.getByTestId("decision-row-dc-task")).toHaveCount(0);

  await page.getByTestId("decision-row-dc-current").click();

  const detail = page.getByTestId("decision-detail");
  await expect(detail).toBeVisible();
  await expect(page.locator(".aq-side-panel .aq-job-detail")).toBeVisible();
  await expect(detail.getByText("Rationale")).toBeVisible();
  await expect(detail).toContainText(
    "Decisions must stay visible in the shell",
  );
  await expect(page.getByTestId("decision-lineage")).toContainText("dc-prev");
  await expect(
    detail.getByRole("button", { name: /Create Decision/i }),
  ).toHaveCount(0);
  await expect(
    detail.getByRole("button", { name: /^Supersede$/i }),
  ).toHaveCount(0);

  await detail.getByRole("button", { name: /dc-prev/i }).click();
  await expect(detail).toContainText(
    "Prototype decisions as a lightweight panel",
  );
  await expect(page.getByTestId("decision-lineage")).toContainText("dc-root");

  await detail.getByRole("link", { name: "job-183" }).click();
  await expect(page).toHaveURL(/\/work\?job=job-183/);
  await expect(page.getByTestId("work-detail")).toContainText("job-183");
});

test("decisions-side-panel opens shared job detail panel", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await openAuthedView(page, "/decisions", {
    decisionsPayload: DECISIONS_FIXTURE,
    decisionLineageById: LINEAGE_FIXTURES,
  });

  await expect(page.locator(".aq-knowledge-list")).toBeVisible();
  await page.getByTestId("decision-row-dc-current").click();

  const panel = page.locator(".aq-side-panel .aq-job-detail");
  await expect(panel).toBeVisible();
  await expect(panel).toHaveAttribute("data-testid", "decision-detail");
  await expect(panel).toContainText("dc-current");
  await expect(panel).toContainText("Rationale");
  await expect(panel.getByRole("button", { name: "Close job detail" })).toBeVisible();

  await page.addStyleTag({
    content: "nextjs-portal { display: none !important; }",
  });
  await page.screenshot({
    path: "test-results/decisions-1440x900-panel.png",
  });
});

test("decisions-keyboard navigates the filtered list and closes panel", async ({
  page,
}) => {
  await openAuthedView(page, "/decisions", {
    decisionsPayload: DECISIONS_FIXTURE,
    decisionLineageById: LINEAGE_FIXTURES,
  });

  const projectScope = page
    .locator(".aq-filter-group")
    .filter({ hasText: "Scope" })
    .getByRole("button", { name: /Project/i });
  await projectScope.click();
  await expect(page.getByTestId("decision-row-dc-current")).toBeVisible();

  await page.keyboard.press("ArrowDown");
  await expect(page.getByTestId("decision-detail")).toContainText("dc-current");

  await page.keyboard.press("ArrowDown");
  await expect(page.getByTestId("decision-detail")).toContainText("dc-prev");

  await page.keyboard.press("Escape");
  await expect(page.locator(".aq-side-panel")).toHaveCount(0);
});
