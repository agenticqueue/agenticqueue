import { expect, test } from "@playwright/test";

import { openAuthedView } from "./helpers";

test("renders the graph view, switches modes, and opens a node detail panel within budget", async ({
  page,
}) => {
  await openAuthedView(page, "/graph", {
    decisionsPayload: buildDecisionsPayload(),
    workPayload: buildWorkPayload(50),
  });

  await expect(page.getByTestId("graph-canvas")).toBeVisible();
  await expect(page.getByRole("tab", { name: "Decision map" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Execution chain" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Dependency map" })).toBeVisible();

  await expect(page.getByTestId("graph-node-job-1")).toBeVisible();
  await expect(page.getByTestId("graph-canvas")).toHaveAttribute(
    "data-first-node-ms",
    /[0-9]/,
  );

  const firstNodeRenderMs = Number(
    (await page.getByTestId("graph-canvas").getAttribute("data-first-node-ms")) ?? "NaN",
  );
  expect(firstNodeRenderMs).toBeLessThan(500);

  const executionTab = page.getByTestId("graph-tab-execution");
  await executionTab.click();
  await expect(executionTab).toHaveAttribute("aria-selected", "true");

  const firstJobNode = page.getByTestId("graph-node-job-1");
  await firstJobNode.click();

  await expect(page.getByTestId("graph-detail")).toBeVisible();
  await expect(page.getByTestId("graph-detail-ref")).toHaveText("job-1");
  await expect(firstJobNode).toHaveClass(/is-selected/);
  await expect(page.getByTestId("graph-node-job-10")).toHaveClass(/is-dimmed/);
});

function buildWorkPayload(count: number) {
  const items = Array.from({ length: count }, (_, index) => {
    const number = index + 1;
    const ref = `job-${number}`;
    const createdAt = new Date(Date.UTC(2026, 3, 21, 14, number % 60, 0)).toISOString();
    const updatedAt = new Date(Date.UTC(2026, 3, 21, 14, (number + 5) % 60, 0)).toISOString();
    const previousRef = number > 1 ? `job-${number - 1}` : null;
    const nextRef = number < count ? `job-${number + 1}` : null;

    return {
      id: `task-${number}`,
      ref,
      title: `Graph node ${number}`,
      pipeline: number <= 25 ? "Phase 7 UI" : "Phase 10 deploy",
      pipeline_slug: number <= 25 ? "phase-7-ui" : "phase-10-deploy",
      actor: number % 3 === 0 ? "codex-hourly" : null,
      claimed_at: number % 3 === 0 ? createdAt : null,
      closed_at: number % 4 === 0 ? updatedAt : null,
      created_at: createdAt,
      updated_at: updatedAt,
      status:
        number === 1
          ? "running"
          : number % 11 === 0
            ? "blocked"
            : number % 5 === 0
              ? "review"
              : number % 2 === 0
                ? "done"
                : "queued",
      raw_state:
        number === 1
          ? "in_progress"
          : number % 11 === 0
            ? "blocked"
            : number % 5 === 0
              ? "submitted"
              : number % 2 === 0
                ? "done"
                : "queued",
      priority: 5 - (number % 5),
      task_type: "coding-task",
      description: `Synthetic-free mocked read model for graph smoke node ${number}.`,
      labels: [`phase:${number <= 25 ? 7 : 10}`, "track:agenticqueue"],
      outputs:
        number % 10 === 0
          ? [
              {
                id: `artifact-${number}`,
                kind: "report",
                label: `artifact-${number}.md`,
                uri: `file:///tmp/artifact-${number}.md`,
                created_at: updatedAt,
                run_ref: `run-${number}`,
              },
            ]
          : [],
      parent_ref: number > 1 ? previousRef : null,
      dependency_refs: previousRef ? [previousRef] : [],
      blocked_by_refs: number % 11 === 0 && previousRef ? [previousRef] : [],
      block_refs: nextRef ? [nextRef] : [],
      child_refs: nextRef ? [nextRef] : [],
    };
  });

  return {
    generated_at: "2026-04-21T14:05:00.000Z",
    count: items.length,
    items,
  };
}

function buildDecisionsPayload() {
  const items = Array.from({ length: 6 }, (_, index) => {
    const number = index + 1;
    return {
      id: `decision-${number}`,
      ref: `DC-${String(number).padStart(3, "0")}`,
      title: `Decision ${number}`,
      scope: number % 2 === 0 ? "project" : "task",
      actor: "codex-hourly",
      decided_at: new Date(Date.UTC(2026, 3, 21, 13, number * 3, 0)).toISOString(),
      status: number === 6 ? "superseded" : "active",
      rationale: `Decision ${number} rationale for graph smoke.`,
      project_name: "AgenticQueue",
      project_slug: "agenticqueue",
      primary_job_ref: `job-${number}`,
      linked_job_refs: [`job-${number}`, `job-${number + 10}`],
      supersedes_refs: number > 1 ? [`DC-${String(number - 1).padStart(3, "0")}`] : [],
      superseded_by_refs:
        number < 6 ? [`DC-${String(number + 1).padStart(3, "0")}`] : [],
    };
  });

  return {
    generated_at: "2026-04-21T14:05:00.000Z",
    count: items.length,
    items,
  };
}
