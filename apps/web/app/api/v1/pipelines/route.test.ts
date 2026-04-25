import { NextRequest } from "next/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import { GET } from "./route";

type JsonBody = Record<string, unknown> | Array<Record<string, unknown>>;

describe("GET /api/v1/pipelines", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("reuses a cached aggregate for the same token and state", async () => {
    const fetchSpy = vi.spyOn(global, "fetch").mockImplementation(async (input) => {
      await delay(80);
      const url = normalizeUrl(input);

      if (url.pathname === "/v1/projects") {
        return jsonResponse([
          projectEntity({
            id: "project-1",
            name: "Realtime ingestion rebuild",
            policyId: "policy-1",
          }),
        ]);
      }

      if (url.pathname === "/v1/tasks" && url.searchParams.get("cursor") === "page-2") {
        return jsonResponse([
          taskEntity({
            id: "task-2",
            projectId: "project-1",
            sequence: 102,
            title: "Execution packet compiler",
            state: "queued",
          }),
        ]);
      }

      if (url.pathname === "/v1/tasks") {
        return jsonResponse(
          [
            taskEntity({
              id: "task-1",
              projectId: "project-1",
              sequence: 101,
              title: "Contract pass on ingest worker",
              state: "in_progress",
            }),
          ],
          {
            headers: {
              "X-Next-Cursor": "page-2",
            },
          },
        );
      }

      if (url.pathname === "/v1/policies") {
        return jsonResponse([
          policyEntity({
            id: "policy-1",
            hitlRequired: true,
          }),
        ]);
      }

      if (url.pathname === "/v1/edges") {
        return jsonResponse([]);
      }

      return jsonResponse(
        { error: `Unhandled URL ${url.pathname}?${url.searchParams.toString()}` },
        { status: 500 },
      );
    });

    const firstStartedAt = performance.now();
    const firstResponse = await GET(
      buildRequest("Bearer cache-token", "in_progress"),
    );
    const firstElapsed = performance.now() - firstStartedAt;

    const firstPayload = (await firstResponse.json()) as {
      generated_at: string;
      pipelines: Array<{ progress: { total: number } }>;
    };

    const secondStartedAt = performance.now();
    const secondResponse = await GET(
      buildRequest("Bearer cache-token", "in_progress"),
    );
    const secondElapsed = performance.now() - secondStartedAt;
    const secondPayload = (await secondResponse.json()) as {
      generated_at: string;
      pipelines: Array<{ progress: { total: number } }>;
    };

    expect(firstResponse.status).toBe(200);
    expect(secondResponse.status).toBe(200);
    expect(firstPayload.pipelines[0]?.progress.total).toBe(2);
    expect(secondPayload).toEqual(firstPayload);
    expect(firstElapsed).toBeGreaterThanOrEqual(80);
    expect(secondElapsed).toBeLessThan(50);
    expect(fetchSpy).toHaveBeenCalledTimes(5);
    expect(secondResponse.headers.get("Cache-Control")).toBe(
      "private, max-age=10, stale-while-revalidate=10",
    );
  });

  it("walks cursor pagination on a cache miss", async () => {
    const fetchSpy = vi.spyOn(global, "fetch").mockImplementation(async (input) => {
      const url = normalizeUrl(input);

      if (url.pathname === "/v1/projects") {
        return jsonResponse([
          projectEntity({
            id: "project-1",
            name: "Execution graph hardening",
            policyId: "policy-1",
          }),
        ]);
      }

      if (url.pathname === "/v1/tasks" && url.searchParams.get("cursor") === "page-2") {
        return jsonResponse([
          taskEntity({
            id: "task-2",
            projectId: "project-1",
            sequence: 202,
            title: "Review queue dashboard",
            state: "blocked",
          }),
        ]);
      }

      if (url.pathname === "/v1/tasks") {
        return jsonResponse(
          [
            taskEntity({
              id: "task-1",
              projectId: "project-1",
              sequence: 201,
              title: "Dependency lane renderer",
              state: "validated",
            }),
          ],
          {
            headers: {
              "X-Next-Cursor": "page-2",
            },
          },
        );
      }

      if (url.pathname === "/v1/policies") {
        return jsonResponse([
          policyEntity({
            id: "policy-1",
            autonomyTier: 2,
          }),
        ]);
      }

      if (url.pathname === "/v1/edges") {
        return jsonResponse([]);
      }

      return jsonResponse(
        { error: `Unhandled URL ${url.pathname}?${url.searchParams.toString()}` },
        { status: 500 },
      );
    });

    const response = await GET(
      buildRequest("Bearer cache-miss-token", "in_progress"),
    );
    const payload = (await response.json()) as {
      count: number;
      pipelines: Array<{ progress: { total: number } }>;
    };

    const requestedUrls = fetchSpy.mock.calls.map(([input]) =>
      normalizeUrl(input).toString(),
    );

    expect(response.status).toBe(200);
    expect(payload.count).toBe(1);
    expect(payload.pipelines[0]?.progress.total).toBe(2);
    expect(requestedUrls).toContain("http://127.0.0.1:8010/v1/tasks?limit=200");
    expect(requestedUrls).toContain(
      "http://127.0.0.1:8010/v1/tasks?limit=200&cursor=page-2",
    );
  });
});

function buildRequest(authorization: string, state: "in_progress" | "done") {
  return new NextRequest(`http://localhost/api/v1/pipelines?state=${state}`, {
    headers: {
      Authorization: authorization,
    },
  });
}

function normalizeUrl(input: string | URL | Request) {
  const rawUrl =
    typeof input === "string"
      ? input
      : input instanceof URL
        ? input.toString()
        : input.url;
  return new URL(rawUrl);
}

function jsonResponse(body: JsonBody, init?: ResponseInit) {
  return new Response(JSON.stringify(body), {
    headers: {
      "Content-Type": "application/json",
    },
    status: 200,
    ...init,
  });
}

function delay(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function projectEntity({
  id,
  name,
  policyId = null,
}: {
  id: string;
  name: string;
  policyId?: string | null;
}) {
  return {
    id,
    workspace_id: "workspace-1",
    policy_id: policyId,
    slug: name.toLowerCase().replace(/\s+/g, "-"),
    name,
    description: `${name} goal`,
    created_at: "2026-04-24T12:00:00.000Z",
    updated_at: "2026-04-24T12:00:00.000Z",
  };
}

function taskEntity({
  id,
  projectId,
  sequence,
  title,
  state,
}: {
  id: string;
  projectId: string;
  sequence: number;
  title: string;
  state: string;
}) {
  return {
    id,
    project_id: projectId,
    policy_id: null,
    task_type: "coding-task",
    title,
    state,
    priority: 2,
    labels: ["phase:7"],
    sequence,
    claimed_by_actor_id: state === "in_progress" ? "actor-1" : null,
    claimed_at:
      state === "in_progress" ? "2026-04-24T12:01:00.000Z" : null,
    description: `${title} description`,
    contract: {},
    definition_of_done: [],
    created_at: "2026-04-24T12:00:00.000Z",
    updated_at: "2026-04-24T12:05:00.000Z",
  };
}

function policyEntity({
  id,
  autonomyTier = 3,
  hitlRequired = false,
}: {
  id: string;
  autonomyTier?: number;
  hitlRequired?: boolean;
}) {
  return {
    id,
    workspace_id: "workspace-1",
    name: `Policy ${id}`,
    version: "1.0.0",
    hitl_required: hitlRequired,
    autonomy_tier: autonomyTier,
    capabilities: ["read_repo"],
    body: {},
    created_at: "2026-04-24T12:00:00.000Z",
    updated_at: "2026-04-24T12:00:00.000Z",
  };
}
