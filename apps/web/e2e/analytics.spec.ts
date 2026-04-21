import { expect, test } from "@playwright/test";

import { openAuthedView } from "./helpers";

const ANALYTICS_FIXTURE = {
  generated_at: "2026-04-21T15:24:00.000Z",
  window: {
    key: "90d",
    days: 90,
    start_at: "2026-01-21T15:24:00.000Z",
    end_at: "2026-04-21T15:24:00.000Z",
  },
  cycle_time: [
    {
      task_type: "coding-task",
      count: 8,
      median_hours: 4.2,
      p95_hours: 11.4,
    },
  ],
  blocked_heatmap: [
    {
      blocker_ref: "job-101",
      blocker_title: "Ship the packet compiler",
      task_count: 2,
      total_blocked_hours: 13.5,
      p95_blocked_hours: 7.2,
      sample_refs: ["job-110", "job-115"],
    },
  ],
  handoff_latency_histogram: [
    { label: "<15m", min_minutes: 0, max_minutes: 15, count: 2 },
    { label: "15-30m", min_minutes: 15, max_minutes: 30, count: 3 },
    { label: "30-60m", min_minutes: 30, max_minutes: 60, count: 1 },
    { label: "60-120m", min_minutes: 60, max_minutes: 120, count: 1 },
    { label: "120m+", min_minutes: 120, max_minutes: null, count: 0 },
  ],
  handoff_latency_by_actor: [
    { actor: "codex", count: 4, median_minutes: 22, p95_minutes: 61 },
    { actor: "gemini", count: 3, median_minutes: 14, p95_minutes: 28 },
  ],
  retrieval_precision: {
    sample_size: 5,
    precision_at_5: 0.72,
    precision_at_10: 0.66,
    note: "Surface-area overlap over packet_version retrieval payloads.",
  },
  agent_success_rates: [
    {
      actor: "codex",
      complete_count: 4,
      parked_count: 1,
      error_count: 1,
      total_count: 6,
      success_rate: 0.67,
    },
    {
      actor: "gemini",
      complete_count: 3,
      parked_count: 0,
      error_count: 0,
      total_count: 3,
      success_rate: 1,
    },
  ],
  review_load: Array.from({ length: 21 }, (_, index) => ({
    day: `2026-04-${String(index + 1).padStart(2, "0")}`,
    count: index % 4,
  })),
};

test("renders the analytics dashboard as a read-only shell", async ({ page }) => {
  await openAuthedView(page, "/analytics", {
    analyticsPayload: ANALYTICS_FIXTURE,
  });

  await expect(
    page.getByRole("heading", { level: 1, name: "Analytics" }),
  ).toBeVisible();
  await expect(page.locator(".aq-analytics-panel")).toHaveCount(6);
  await expect(page.getByText("Packet-backed relevance quality")).toBeVisible();
  await expect(page.getByText("Outcome mix by actor")).toBeVisible();
  await expect(page.getByText("Daily review queue pressure")).toBeVisible();
  await expect(page.getByRole("button", { name: /approve/i })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /reject/i })).toHaveCount(0);
});
