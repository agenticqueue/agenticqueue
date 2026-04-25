import fs from "node:fs/promises";
import path from "node:path";

import { expect, test } from "@playwright/test";

import { mockShellReadApis, seedAuthenticatedSession } from "./helpers";

const BREAKPOINTS = [
  { name: "iphone-se", width: 375, height: 667 },
  { name: "ipad-portrait", width: 768, height: 1024 },
  { name: "ipad-landscape", width: 1024, height: 768 },
  { name: "macbook", width: 1440, height: 900 },
  { name: "desktop-fhd", width: 1920, height: 1080 },
] as const;

const VIEWS = [
  { name: "pipelines", path: "/pipelines", heading: "Pipelines" },
  { name: "work", path: "/work", heading: "Work" },
  { name: "analytics", path: "/analytics", heading: "Analytics" },
  { name: "graph", path: "/graph", heading: "Graph" },
  { name: "decisions", path: "/decisions", heading: "Decisions" },
  { name: "learnings", path: "/learnings", heading: "Learnings" },
  { name: "settings", path: "/settings", heading: "Settings" },
] as const;

const README_PATH = path.resolve(__dirname, "..", "README.md");
const ARTIFACT_DIR = path.resolve(__dirname, "..", "test-results", "responsive-baseline");
const SUMMARY_PATH = path.join(ARTIFACT_DIR, "audit-summary.json");

const WORK_PAYLOAD = {
  generated_at: "2026-04-24T11:55:00.000Z",
  count: 2,
  items: [
    {
      id: "task-104",
      ref: "job-104",
      title: "Responsive shell audit baseline",
      pipeline: "Phase 7 - Web UI",
      pipeline_slug: "phase-7-web-ui",
      actor: "codex",
      claimed_at: "2026-04-24T11:40:00.000Z",
      closed_at: null,
      created_at: "2026-04-24T11:10:00.000Z",
      updated_at: "2026-04-24T11:50:00.000Z",
      status: "running",
      raw_state: "in_progress",
      priority: 5,
      task_type: "coding-task",
      description: "Read-only responsive audit fixture.",
      labels: ["phase:7", "needs:coding", "agent:codex"],
      outputs: [],
      decisions: [],
      activity: [],
      parent_ref: null,
      dependency_refs: [],
      blocked_by_refs: [],
      block_refs: ["job-115"],
      child_refs: [],
    },
    {
      id: "task-115",
      ref: "job-115",
      title: "Fix mobile overflow in the read-only shell",
      pipeline: "Phase 7 - Web UI",
      pipeline_slug: "phase-7-web-ui",
      actor: null,
      claimed_at: null,
      closed_at: null,
      created_at: "2026-04-24T11:20:00.000Z",
      updated_at: "2026-04-24T11:45:00.000Z",
      status: "queued",
      raw_state: "queued",
      priority: 4,
      task_type: "coding-task",
      description: "Queued follow-up fixture for the responsive audit shell.",
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
  ],
};

const DECISIONS_PAYLOAD = {
  generated_at: "2026-04-24T11:55:00.000Z",
  count: 2,
  items: [
    {
      id: "decision-current",
      ref: "dc-current",
      title: "Audit the shell at fixed breakpoints before shipping more UI work",
      scope: "project",
      actor: "codex",
      decided_at: "2026-04-24T11:30:00.000Z",
      status: "active",
      rationale: "The shell needs viewport evidence before more layout work lands.",
      project_name: "Phase 7 - Web UI",
      project_slug: "phase-7-web-ui",
      primary_job_ref: "job-104",
      linked_job_refs: ["job-104", "job-115"],
      supersedes_refs: [],
      superseded_by_refs: [],
      alternative_refs: [],
    },
    {
      id: "decision-alt",
      ref: "dc-alt",
      title: "Keep layout QA ad hoc",
      scope: "project",
      actor: "ghost",
      decided_at: "2026-04-23T17:00:00.000Z",
      status: "superseded",
      rationale: "Ad hoc QA misses regressions across the shell views.",
      project_name: "Phase 7 - Web UI",
      project_slug: "phase-7-web-ui",
      primary_job_ref: "job-115",
      linked_job_refs: ["job-115"],
      supersedes_refs: [],
      superseded_by_refs: ["dc-current"],
      alternative_refs: [],
    },
  ],
};

const ANALYTICS_PAYLOAD = {
  generated_at: "2026-04-24T11:55:00.000Z",
  window: {
    key: "90d",
    days: 90,
    start_at: "2026-01-24T11:55:00.000Z",
    end_at: "2026-04-24T11:55:00.000Z",
  },
  cycle_time: [{ task_type: "coding-task", count: 4, median_hours: 2.8, p95_hours: 7.4 }],
  blocked_heatmap: [
    {
      blocker_ref: "job-115",
      blocker_title: "Fix mobile overflow in the read-only shell",
      task_count: 2,
      total_blocked_hours: 5.5,
      p95_blocked_hours: 3.2,
      sample_refs: ["job-120"],
    },
  ],
  handoff_latency_histogram: [{ label: "<15m", min_minutes: 0, max_minutes: 15, count: 2 }],
  handoff_latency_by_actor: [{ actor: "codex", count: 4, median_minutes: 11, p95_minutes: 18 }],
  retrieval_precision: {
    sample_size: 3,
    precision_at_5: 0.71,
    precision_at_10: 0.67,
    note: "Responsive audit fixture.",
  },
  agent_success_rates: [
    {
      actor: "codex",
      complete_count: 3,
      parked_count: 1,
      error_count: 0,
      total_count: 4,
      success_rate: 0.75,
    },
  ],
  review_load: Array.from({ length: 21 }, (_, index) => ({
    day: `2026-04-${String(index + 1).padStart(2, "0")}`,
    count: index % 3,
  })),
};

type AuditResult = {
  view: (typeof VIEWS)[number]["name"];
  breakpoint: (typeof BREAKPOINTS)[number]["name"];
  viewport: {
    width: number;
    height: number;
  };
  screenshotPath: string;
  overflowX: boolean;
  rightOverflowSelectors: string[];
  consoleErrors: string[];
  pageErrors: string[];
};

test("captures the responsive baseline for every shell view", async ({ page }) => {
  test.setTimeout(180_000);

  await fs.mkdir(ARTIFACT_DIR, { recursive: true });
  const results: AuditResult[] = [];

  await seedAuthenticatedSession(page);
  await mockShellReadApis(page, {
    analyticsPayload: ANALYTICS_PAYLOAD,
    decisionsPayload: DECISIONS_PAYLOAD,
  });
  await page.route("**/api/v1/work**", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      json: WORK_PAYLOAD,
      status: 200,
    });
  });
  await page.route("**/api/v1/nav-counts", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      json: {
        pipelines: 2,
        work: 2,
        analytics: 6,
        graph: 4,
        decisions: 2,
        learnings: 3,
      },
      status: 200,
    });
  });
  await page.route("**/api/health", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      json: {
        status: "ok",
        deps: {
          api: {
            status: "ok",
            http_status: 200,
          },
        },
      },
      status: 200,
    });
  });

  const consoleErrors: string[] = [];
  const pageErrors: string[] = [];
  const handleConsole = (message: { type(): string; text(): string }) => {
    if (message.type() === "error") {
      consoleErrors.push(message.text());
    }
  };
  const handlePageError = (error: Error) => {
    pageErrors.push(error.message);
  };

  page.on("console", handleConsole);
  page.on("pageerror", handlePageError);

  for (const breakpoint of BREAKPOINTS) {
    await page.setViewportSize({
      width: breakpoint.width,
      height: breakpoint.height,
    });

    for (const view of VIEWS) {
      consoleErrors.length = 0;
      pageErrors.length = 0;

      await page.goto(view.path);
      await expect(
        page.getByRole("heading", { level: 1, name: view.heading }),
      ).toBeVisible();
      await expect(page.getByRole("navigation", { name: "Primary" })).toBeVisible();
      await expect(page.getByRole("link", { name: "Settings" })).toBeVisible();
      await page.waitForTimeout(200);

      const layout = await page.evaluate(() => {
        const root = document.documentElement;
        const viewportWidth = window.innerWidth;
        const rightOverflowSelectors = Array.from(
          document.body.querySelectorAll<HTMLElement>("body *"),
        )
          .map((element) => {
            const rect = element.getBoundingClientRect();
            if (rect.width === 0 || rect.height === 0) {
              return null;
            }
            if (rect.right <= viewportWidth + 1) {
              return null;
            }
            const testId = element.dataset.testid ? `[data-testid="${element.dataset.testid}"]` : "";
            const className =
              typeof element.className === "string" && element.className.trim().length > 0
                ? `.${element.className.trim().split(/\s+/).join(".")}`
                : "";
            return `${element.tagName.toLowerCase()}${testId || className}`;
          })
          .filter((selector): selector is string => Boolean(selector))
          .slice(0, 8);

        return {
          overflowX: root.scrollWidth > viewportWidth + 1,
          rightOverflowSelectors,
        };
      });

      const screenshotPath = path.join(
        ARTIFACT_DIR,
        `${view.name}-${breakpoint.name}.png`,
      );
      await page.screenshot({
        fullPage: true,
        path: screenshotPath,
      });

      results.push({
        view: view.name,
        breakpoint: breakpoint.name,
        viewport: {
          width: breakpoint.width,
          height: breakpoint.height,
        },
        screenshotPath,
        overflowX: layout.overflowX,
        rightOverflowSelectors: layout.rightOverflowSelectors,
        consoleErrors: [...consoleErrors],
        pageErrors: [...pageErrors],
      });
    }
  }

  page.removeListener("console", handleConsole);
  page.removeListener("pageerror", handlePageError);

  await fs.writeFile(SUMMARY_PATH, JSON.stringify(results, null, 2), "utf8");

  const failures = results.filter(
    (result) =>
      result.consoleErrors.length > 0 ||
      result.pageErrors.length > 0,
  );
  const analyticsOverflowFailures = results
    .filter((result) => result.view === "analytics" && result.overflowX)
    .map((result) => ({
      breakpoint: result.breakpoint,
      viewport: result.viewport,
      rightOverflowSelectors: result.rightOverflowSelectors,
    }));

  expect(
    failures,
    `responsive audit hit console/page errors: ${JSON.stringify(failures, null, 2)}`,
  ).toEqual([]);
  expect(
    analyticsOverflowFailures,
    `analytics should not overflow horizontally at audited breakpoints: ${JSON.stringify(
      analyticsOverflowFailures,
      null,
      2,
    )}`,
  ).toEqual([]);
});

test("documents the supported responsive breakpoint matrix", async () => {
  const readme = await fs.readFile(README_PATH, "utf8");

  expect(readme).toContain("## Supported Breakpoints");

  for (const breakpoint of BREAKPOINTS) {
    expect(readme).toContain(`${breakpoint.width}`);
    expect(readme).toContain(`${breakpoint.height}`);
  }
});
