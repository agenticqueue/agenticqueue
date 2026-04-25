import http from "node:http";

const port = Number(process.env.AQ_E2E_AUTH_API_PORT ?? "3127");

const state = {
  needs_bootstrap: false,
  pipelines_delay_ms: 0,
};

const pipelineFixture = {
  projects: [
    {
      id: "pipeline-1",
      workspace_id: "workspace-1",
      policy_id: "policy-1",
      slug: "realtime-ingestion-rebuild",
      name: "Realtime ingestion rebuild",
      description: "Stabilize the ingest chain before the broader execution rollout.",
      created_at: "2026-04-21T12:40:00.000Z",
      updated_at: "2026-04-21T13:00:00.000Z",
    },
    {
      id: "pipeline-2",
      workspace_id: "workspace-1",
      policy_id: "policy-1",
      slug: "launch-readiness",
      name: "Customer launch prep",
      description: "Keep the launch checklist visible once contracts are complete.",
      created_at: "2026-04-21T10:00:00.000Z",
      updated_at: "2026-04-21T11:30:00.000Z",
    },
  ],
  tasks: [
    {
      id: "task-101",
      project_id: "pipeline-1",
      policy_id: null,
      task_type: "coding-task",
      title: "Contract pass on ingest worker",
      state: "done",
      priority: 2,
      labels: ["phase:7"],
      sequence: 101,
      claimed_by_actor_id: "actor-1",
      claimed_at: "2026-04-21T12:46:00.000Z",
      description: "Finish the worker contract and publish the packet.",
      contract: {},
      definition_of_done: [],
      created_at: "2026-04-21T12:40:00.000Z",
      updated_at: "2026-04-21T12:50:00.000Z",
    },
    {
      id: "task-102",
      project_id: "pipeline-1",
      policy_id: null,
      task_type: "coding-task",
      title: "Execution packet compiler",
      state: "in_progress",
      priority: 2,
      labels: ["phase:7", "needs:coding"],
      sequence: 102,
      claimed_by_actor_id: "actor-1",
      claimed_at: "2026-04-21T12:52:00.000Z",
      description: "Compile the packet and keep the shape stable for DAG consumers.",
      contract: {},
      definition_of_done: [],
      created_at: "2026-04-21T12:48:00.000Z",
      updated_at: "2026-04-21T13:00:00.000Z",
    },
    {
      id: "task-301",
      project_id: "pipeline-2",
      policy_id: null,
      task_type: "coding-task",
      title: "Docs readiness pass",
      state: "done",
      priority: 1,
      labels: ["phase:7"],
      sequence: 301,
      claimed_by_actor_id: "actor-1",
      claimed_at: "2026-04-21T10:00:00.000Z",
      description: "Make the final docs pass visible from the launch pipeline.",
      contract: {},
      definition_of_done: [],
      created_at: "2026-04-21T09:00:00.000Z",
      updated_at: "2026-04-21T10:30:00.000Z",
    },
    {
      id: "task-302",
      project_id: "pipeline-2",
      policy_id: null,
      task_type: "coding-task",
      title: "Release note packet",
      state: "done",
      priority: 1,
      labels: ["phase:7"],
      sequence: 302,
      claimed_by_actor_id: "actor-1",
      claimed_at: "2026-04-21T10:35:00.000Z",
      description: "Publish the release packet after docs lock.",
      contract: {},
      definition_of_done: [],
      created_at: "2026-04-21T10:31:00.000Z",
      updated_at: "2026-04-21T11:30:00.000Z",
    },
  ],
  policies: [
    {
      id: "policy-1",
      workspace_id: "workspace-1",
      name: "Default coding",
      version: "1.0.0",
      hitl_required: false,
      autonomy_tier: 3,
      capabilities: ["read_repo", "write_branch"],
      body: {},
      created_at: "2026-04-21T09:00:00.000Z",
      updated_at: "2026-04-21T09:00:00.000Z",
    },
  ],
  edges: [
    {
      id: "edge-1",
      src_entity_type: "task",
      src_id: "task-101",
      dst_entity_type: "task",
      dst_id: "task-102",
      relation: "parent_of",
      metadata: {},
      created_at: "2026-04-21T12:50:00.000Z",
    },
    {
      id: "edge-2",
      src_entity_type: "task",
      src_id: "task-301",
      dst_entity_type: "task",
      dst_id: "task-302",
      relation: "parent_of",
      metadata: {},
      created_at: "2026-04-21T10:30:00.000Z",
    },
  ],
};

function sendJson(response, status, payload, headers = {}) {
  response.writeHead(status, {
    "content-type": "application/json",
    ...headers,
  });
  response.end(JSON.stringify(payload));
}

function maybeSendDelayedJson(response, status, payload, headers = {}) {
  const delayMs = Number(state.pipelines_delay_ms || 0);
  if (delayMs <= 0) {
    sendJson(response, status, payload, headers);
    return;
  }

  setTimeout(() => {
    sendJson(response, status, payload, headers);
  }, delayMs);
}

const server = http.createServer((request, response) => {
  const url = new URL(request.url ?? "/", `http://${request.headers.host}`);

  if (request.method === "GET" && url.pathname === "/v1/health") {
    sendJson(response, 200, { status: "ok" });
    return;
  }

  if (request.method === "GET" && url.pathname === "/api/auth/bootstrap_status") {
    sendJson(response, 200, { needs_bootstrap: state.needs_bootstrap });
    return;
  }

  if (request.method === "POST" && url.pathname === "/__aq_e2e/state") {
    let body = "";
    request.on("data", (chunk) => {
      body += chunk;
    });
    request.on("end", () => {
      const payload = JSON.parse(body || "{}");
      state.needs_bootstrap = Boolean(payload.needs_bootstrap);
      state.pipelines_delay_ms = Number(payload.pipelines_delay_ms ?? state.pipelines_delay_ms ?? 0);
      sendJson(response, 200, state);
    });
    return;
  }

  if (request.method === "POST" && url.pathname === "/api/session") {
    sendJson(
      response,
      200,
      {
        user: { email: "admin@example.com", is_admin: true },
      },
      { "set-cookie": "aq_session=playwright; Path=/; HttpOnly; SameSite=Lax" },
    );
    return;
  }

  if (request.method === "GET" && url.pathname === "/v1/projects") {
    maybeSendDelayedJson(response, 200, pipelineFixture.projects);
    return;
  }

  if (request.method === "GET" && url.pathname === "/v1/tasks") {
    maybeSendDelayedJson(response, 200, pipelineFixture.tasks);
    return;
  }

  if (request.method === "GET" && url.pathname === "/v1/policies") {
    maybeSendDelayedJson(response, 200, pipelineFixture.policies);
    return;
  }

  if (request.method === "GET" && url.pathname === "/v1/edges") {
    maybeSendDelayedJson(response, 200, pipelineFixture.edges);
    return;
  }

  sendJson(response, 404, { error: "Not found" });
});

server.listen(port, "127.0.0.1", () => {
  console.log(`AQ e2e auth API listening on http://127.0.0.1:${port}`);
});
