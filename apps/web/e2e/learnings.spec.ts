import { expect, test } from "@playwright/test";

import { seedAuthenticatedSession } from "./helpers";

const learningsPayload = {
  query: "",
  count: 3,
  generated_at: "2026-04-21T14:07:00.000Z",
  items: [
    {
      id: "learning-1",
      ref: "lrn-a1111111",
      title: "Keep browser smoke coverage tied to deterministic local data",
      scope: "task",
      tier: 1,
      confidence: "confirmed",
      status: "active",
      last_applied: "2026-04-21T13:10:00.000Z",
      owner: "example-admin",
      review_date: "2026-05-20",
      context: {
        what_happened:
          "A browser shell regression was caught before Phase 7 reached review.",
        what_learned:
          "Deterministic fixtures keep the read-only shell smoke tests stable.",
        action_rule:
          "Mock the browser-facing transport and assert the UI contract directly.",
        applies_when: "A Phase 7 view renders queue state or learnings in the shell.",
        does_not_apply_when: "The surface is a static marketing page.",
      },
      evidence: [
        "artifact://playwright-smoke",
        "apps/web/e2e/learnings.spec.ts",
      ],
      applied_in: [
        {
          task_id: "task-101",
          ref: "job-101",
          title: "Render read-only learnings shell",
          href: "/work?job=job-101",
        },
      ],
    },
    {
      id: "learning-2",
      ref: "lrn-b2222222",
      title: "Promote repeated UI verification patterns to project scope",
      scope: "project",
      tier: 2,
      confidence: "validated",
      status: "active",
      last_applied: "2026-04-20T16:10:00.000Z",
      owner: "example-admin",
      review_date: "2026-05-20",
      context: {
        what_happened:
          "Multiple Phase 7 tickets reused the same read-only validation pattern.",
        what_learned:
          "Shared shell checks belong in project memory once the evidence repeats.",
        action_rule:
          "Promote repeated UI smoke patterns after they land in more than one route.",
        applies_when: "Two or more read-only views share the same validation shape.",
        does_not_apply_when:
          "A route still depends on an unbuilt upstream view and is marked fixme.",
      },
      evidence: ["artifact://phase-7-review"],
      applied_in: [
        {
          task_id: "task-102",
          ref: "job-102",
          title: "Broaden shell navigation smoke",
          href: "/work?job=job-102",
        },
      ],
    },
    {
      id: "learning-3",
      ref: "lrn-c3333333",
      title: "Old shell workaround superseded by the shared Phase 7 components",
      scope: "global",
      tier: 3,
      confidence: "tentative",
      status: "superseded",
      last_applied: "2026-04-19T15:00:00.000Z",
      owner: "example-admin",
      review_date: "2026-05-20",
      context: {
        what_happened:
          "Legacy shell behavior was carried forward longer than necessary.",
        what_learned:
          "Shared route shells make one-off browser workarounds unnecessary.",
        action_rule:
          "Retire shell-specific hacks once the shared components cover the route.",
        applies_when:
          "A route graduates from placeholder copy to the shared read-only shell.",
        does_not_apply_when:
          "The UI surface is still intentionally stubbed behind a fixme smoke test.",
      },
      evidence: ["artifact://legacy-shell"],
      applied_in: [],
    },
  ],
};

test.beforeEach(async ({ page }) => {
  await seedAuthenticatedSession(page);

  await page.route("**/api/v1/learnings/search**", async (route) => {
    const query = new URL(route.request().url()).searchParams.get("query") ?? "";
    await route.fulfill({
      contentType: "application/json",
      json: {
        ...learningsPayload,
        query,
      },
      status: 200,
    });
  });
});

test("renders the learnings browser and opens the detail panel", async ({ page }) => {
  await page.goto("/learnings");

  await expect(
    page.getByRole("heading", { level: 1, name: "Learnings" }),
  ).toBeVisible();

  const rows = page.locator("[data-testid^='learning-row-']");
  await expect(rows.first()).toBeVisible();

  const tierOneFilter = page
    .locator(".aq-filter-group", { hasText: "Tier" })
    .getByRole("button", { name: /Tier 1/i });
  await tierOneFilter.click();
  await expect(rows).toHaveCount(1);
  await expect(page.getByText(/1 visible/i)).toBeVisible();

  await rows.first().click();

  const detail = page.getByTestId("learning-detail");
  await expect(detail).toBeVisible();
  await expect(detail.getByText("Evidence")).toBeVisible();
  await expect(detail.locator("li").first()).toBeVisible();
  await expect(detail.getByText("Applied in")).toBeVisible();

  await expect(detail.getByRole("button", { name: /^Promote$/i })).toHaveCount(0);
  await expect(detail.getByRole("button", { name: /^Supersede$/i })).toHaveCount(0);
});
