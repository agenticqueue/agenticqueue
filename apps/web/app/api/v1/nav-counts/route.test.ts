import { NextRequest } from "next/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import { GET } from "./route";

type JsonBody = Record<string, unknown>;

describe("GET /api/v1/nav-counts", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("aggregates live nav counts from existing read-model endpoints", async () => {
    vi.spyOn(global, "fetch").mockImplementation(
      async (input: string | URL | Request) => {
        const rawUrl =
          typeof input === "string"
            ? input
            : input instanceof URL
              ? input.toString()
              : input.url;
        const url = new URL(rawUrl);

        if (url.pathname === "/api/v1/pipelines" && url.searchParams.get("state") === "in_progress") {
          return jsonResponse({
            count: 2,
            pipelines: [],
          });
        }

        if (url.pathname === "/api/v1/pipelines" && url.searchParams.get("state") === "done") {
          return jsonResponse({
            count: 1,
            pipelines: [],
          });
        }

        if (url.pathname === "/api/v1/work") {
          return jsonResponse({
            count: 5,
            items: [
              { ref: "job-101" },
              { ref: "job-102" },
              { ref: "job-103" },
            ],
          });
        }

        if (url.pathname === "/api/v1/analytics/metrics") {
          return jsonResponse({
            cycle_time: [{ task_type: "build" }],
            blocked_heatmap: [{ blocker_ref: "job-1" }],
            handoff_latency_histogram: [{ label: "<15m" }],
            handoff_latency_by_actor: [{ actor: "codex" }],
            retrieval_precision: {
              sample_size: 12,
            },
            agent_success_rates: [{ actor: "codex" }],
            review_load: [{ day: "2026-04-24", count: 3 }],
          });
        }

        if (url.pathname === "/api/v1/decisions") {
          return jsonResponse({
            count: 3,
            items: [
              {
                id: "dc-1",
                ref: "dc-1",
                primary_job_ref: "job-101",
                linked_job_refs: ["job-102"],
              },
              {
                id: "dc-2",
                ref: "dc-2",
                primary_job_ref: "job-102",
                linked_job_refs: ["job-103"],
              },
              {
                id: "dc-3",
                ref: "dc-3",
                primary_job_ref: null,
                linked_job_refs: [],
              },
            ],
          });
        }

        if (url.pathname === "/api/v1/learnings/search") {
          return jsonResponse({
            count: 7,
            items: [],
          });
        }

        return jsonResponse(
          {
            error: `Unhandled URL ${url.pathname}?${url.searchParams.toString()}`,
          },
          { status: 500 },
        );
      },
    );

    const response = await GET(
      new NextRequest("http://localhost/api/v1/nav-counts", {
        headers: {
          Authorization: "Bearer test-token",
        },
      }),
    );

    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({
      pipelines: 3,
      work: 5,
      analytics: 6,
      graph: 6,
      decisions: 3,
      learnings: 7,
    });
  });
});

function jsonResponse(body: JsonBody, init?: ResponseInit) {
  return new Response(JSON.stringify(body), {
    headers: {
      "Content-Type": "application/json",
    },
    status: 200,
    ...init,
  });
}
