import { NextRequest, NextResponse } from "next/server";

const API_BASE_URL =
  process.env.AGENTICQUEUE_API_BASE_URL ??
  process.env.NEXT_PUBLIC_AGENTICQUEUE_API_BASE_URL ??
  "http://127.0.0.1:8010";

const PAGE_LIMIT = 200;

type WorkStatus = "running" | "failed" | "review" | "queued" | "blocked" | "done";

type ActorEntity = {
  id: string;
  handle: string;
  actor_type: string;
  display_name: string;
  auth_subject: string | null;
  is_active: boolean;
  created_at: string;
  updated_at: string;
};

type ProjectEntity = {
  id: string;
  workspace_id: string;
  policy_id: string | null;
  slug: string;
  name: string;
  description: string | null;
  created_at: string;
  updated_at: string;
};

type TaskEntity = {
  id: string;
  project_id: string;
  policy_id: string | null;
  task_type: string;
  title: string;
  state: string;
  priority: number;
  labels: string[];
  sequence: number | null;
  claimed_by_actor_id: string | null;
  claimed_at: string | null;
  description: string | null;
  contract: Record<string, unknown>;
  definition_of_done: string[];
  attempt_count: number;
  last_failure: Record<string, unknown> | null;
  max_attempts: number;
  remaining_attempts: number;
  created_at: string;
  updated_at: string;
};

type RunEntity = {
  id: string;
  task_id: string;
  packet_version_id: string | null;
  actor_id: string | null;
  status: string;
  started_at: string;
  ended_at: string | null;
  summary: string | null;
  details: Record<string, unknown>;
  created_at: string;
  updated_at: string;
};

type ArtifactEntity = {
  id: string;
  task_id: string;
  run_id: string | null;
  kind: string;
  uri: string;
  details: Record<string, unknown>;
  embedding: number[] | null;
  created_at: string;
  updated_at: string;
};

type DecisionEntity = {
  id: string;
  task_id: string;
  run_id: string | null;
  actor_id: string | null;
  summary: string;
  rationale: string | null;
  decided_at: string;
  embedding: number[] | null;
  created_at: string;
};

type EdgeEntity = {
  id: string;
  src_entity_type: string;
  src_id: string;
  dst_entity_type: string;
  dst_id: string;
  relation: string;
  metadata?: Record<string, unknown>;
  created_at: string;
  updated_at: string;
};

type WorkOutput = {
  id: string;
  kind: string;
  label: string;
  uri: string;
  created_at: string;
  run_ref: string | null;
};

type WorkDecision = {
  id: string;
  summary: string;
  rationale: string | null;
  decided_at: string;
  actor: string | null;
  run_ref: string | null;
};

type WorkActivityEntry = {
  id: string;
  label: string;
  summary: string;
  happened_at: string;
  state: WorkStatus | null;
  source: "task" | "run" | "decision" | "artifact";
  command: string | null;
};

type WorkItem = {
  id: string;
  ref: string;
  title: string;
  pipeline: string;
  pipeline_slug: string;
  actor: string | null;
  claimed_at: string | null;
  closed_at: string | null;
  created_at: string;
  updated_at: string;
  status: WorkStatus;
  raw_state: string;
  priority: number;
  task_type: string;
  description: string | null;
  labels: string[];
  outputs: WorkOutput[];
  decisions: WorkDecision[];
  activity: WorkActivityEntry[];
  parent_ref: string | null;
  dependency_refs: string[];
  blocked_by_refs: string[];
  block_refs: string[];
  child_refs: string[];
};

type WorkResponse = {
  generated_at: string;
  count: number;
  items: WorkItem[];
};

type RelationAccumulator = {
  parentId: string | null;
  dependencyIds: Set<string>;
  blockedByIds: Set<string>;
  blockIds: Set<string>;
  childIds: Set<string>;
};

class UpstreamError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "UpstreamError";
    this.status = status;
  }
}

export async function GET(request: NextRequest) {
  const authorization = request.headers.get("authorization")?.trim();
  if (!authorization) {
    return NextResponse.json(
      { error: "Authorization header is required." },
      { status: 401 },
    );
  }

  try {
    const [actors, projects, tasks, runs, artifacts, decisions, edges] =
      await Promise.all([
        fetchAllPages<ActorEntity>({
          path: "/v1/actors",
          authorization,
          signal: request.signal,
        }),
        fetchAllPages<ProjectEntity>({
          path: "/v1/projects",
          authorization,
          signal: request.signal,
        }),
        fetchAllPages<TaskEntity>({
          path: "/v1/tasks",
          authorization,
          signal: request.signal,
        }),
        fetchAllPages<RunEntity>({
          path: "/v1/runs",
          authorization,
          signal: request.signal,
        }),
        fetchAllPages<ArtifactEntity>({
          path: "/v1/artifacts",
          authorization,
          signal: request.signal,
        }),
        fetchAllPages<DecisionEntity>({
          path: "/v1/decisions",
          authorization,
          signal: request.signal,
        }),
        fetchAllPages<EdgeEntity>({
          path: "/v1/edges",
          authorization,
          signal: request.signal,
        }),
      ]);

    const items = buildWorkItems({
      actors,
      projects,
      tasks,
      runs,
      artifacts,
      decisions,
      edges,
    });

    return NextResponse.json<WorkResponse>({
      generated_at: new Date().toISOString(),
      count: items.length,
      items,
    });
  } catch (error: unknown) {
    if (error instanceof UpstreamError) {
      return NextResponse.json(
        { error: error.message, status: error.status },
        { status: error.status },
      );
    }

    return NextResponse.json(
      {
        error:
          error instanceof Error
            ? error.message
            : "Failed to aggregate work view.",
      },
      { status: 500 },
    );
  }
}

async function fetchAllPages<T>({
  path,
  authorization,
  signal,
}: {
  path: string;
  authorization: string;
  signal: AbortSignal;
}): Promise<T[]> {
  const items: T[] = [];
  let cursor: string | null = null;

  do {
    const url = new URL(path, API_BASE_URL);
    url.searchParams.set("limit", String(PAGE_LIMIT));
    if (cursor) {
      url.searchParams.set("cursor", cursor);
    }

    const response = await fetch(url, {
      headers: {
        Authorization: authorization,
      },
      cache: "no-store",
      signal,
    });

    const payload = (await response.json().catch(() => null)) as
      | T[]
      | { error?: string; message?: string }
      | null;

    if (!response.ok || !Array.isArray(payload)) {
      const detail =
        payload && !Array.isArray(payload) && typeof payload === "object"
          ? payload.error ?? payload.message
          : null;
      throw new UpstreamError(
        detail ?? `Upstream request failed for ${path}.`,
        response.status || 500,
      );
    }

    items.push(...payload);
    cursor = response.headers.get("X-Next-Cursor");
  } while (cursor);

  return items;
}

function buildWorkItems({
  actors,
  projects,
  tasks,
  runs,
  artifacts,
  decisions,
  edges,
}: {
  actors: ActorEntity[];
  projects: ProjectEntity[];
  tasks: TaskEntity[];
  runs: RunEntity[];
  artifacts: ArtifactEntity[];
  decisions: DecisionEntity[];
  edges: EdgeEntity[];
}): WorkItem[] {
  const actorsById = new Map(actors.map((actor) => [actor.id, actor]));
  const projectsById = new Map(projects.map((project) => [project.id, project]));
  const tasksById = new Map(tasks.map((task) => [task.id, task]));
  const runsByTaskId = groupBy(runs, (run) => run.task_id);
  const artifactsByTaskId = groupBy(artifacts, (artifact) => artifact.task_id);
  const decisionsByTaskId = groupBy(decisions, (decision) => decision.task_id);
  const activeEdges = edges.filter(
    (edge) =>
      edgeIsActive(edge) &&
      edge.src_entity_type === "task" &&
      edge.dst_entity_type === "task" &&
      tasksById.has(edge.src_id) &&
      tasksById.has(edge.dst_id),
  );
  const relationsByTaskId = new Map<string, RelationAccumulator>(
    tasks.map((task) => [
      task.id,
      {
        parentId: null,
        dependencyIds: new Set<string>(),
        blockedByIds: new Set<string>(),
        blockIds: new Set<string>(),
        childIds: new Set<string>(),
      },
    ]),
  );

  for (const edge of activeEdges) {
    const source = relationsByTaskId.get(edge.src_id);
    const target = relationsByTaskId.get(edge.dst_id);
    if (!source || !target) {
      continue;
    }

    switch (edge.relation) {
      case "parent_of":
        source.childIds.add(edge.dst_id);
        target.parentId = edge.src_id;
        break;
      case "depends_on":
        source.dependencyIds.add(edge.dst_id);
        break;
      case "blocks":
        source.blockIds.add(edge.dst_id);
        target.blockedByIds.add(edge.src_id);
        break;
      case "gated_by":
        source.blockedByIds.add(edge.dst_id);
        break;
      case "unblocks":
        source.blockIds.add(edge.dst_id);
        break;
      default:
        break;
    }
  }

  return tasks
    .map((task) =>
      serializeTask({
        task,
        actorsById,
        projectsById,
        tasksById,
        relationsByTaskId,
        taskRuns: runsByTaskId.get(task.id) ?? [],
        taskArtifacts: artifactsByTaskId.get(task.id) ?? [],
        taskDecisions: decisionsByTaskId.get(task.id) ?? [],
      }),
    )
    .sort(compareWorkItems);
}

function serializeTask({
  task,
  actorsById,
  projectsById,
  tasksById,
  relationsByTaskId,
  taskRuns,
  taskArtifacts,
  taskDecisions,
}: {
  task: TaskEntity;
  actorsById: Map<string, ActorEntity>;
  projectsById: Map<string, ProjectEntity>;
  tasksById: Map<string, TaskEntity>;
  relationsByTaskId: Map<string, RelationAccumulator>;
  taskRuns: RunEntity[];
  taskArtifacts: ArtifactEntity[];
  taskDecisions: DecisionEntity[];
}) : WorkItem {
  const project = projectsById.get(task.project_id);
  const actor = task.claimed_by_actor_id
    ? actorsById.get(task.claimed_by_actor_id) ?? null
    : null;
  const runs = [...taskRuns].sort(
    (left, right) =>
      (right.ended_at ?? right.started_at).localeCompare(
        left.ended_at ?? left.started_at,
      ),
  );
  const artifacts = [...taskArtifacts].sort((left, right) =>
    right.created_at.localeCompare(left.created_at),
  );
  const decisions = [...taskDecisions].sort((left, right) =>
    right.decided_at.localeCompare(left.decided_at),
  );
  const relations = relationsByTaskId.get(task.id);

  return {
    id: task.id,
    ref: taskRef(task),
    title: task.title,
    pipeline: project?.name ?? "Unknown pipeline",
    pipeline_slug: project?.slug ?? "unknown",
    actor: actor?.handle ?? null,
    claimed_at: task.claimed_at,
    closed_at: deriveClosedAt(task, runs),
    created_at: task.created_at,
    updated_at: task.updated_at,
    status: mapTaskState(task.state),
    raw_state: task.state,
    priority: task.priority,
    task_type: task.task_type,
    description: task.description,
    labels: task.labels,
    outputs: artifacts.map((artifact) => ({
      id: artifact.id,
      kind: artifact.kind,
      label: artifactLabel(artifact),
      uri: artifact.uri,
      created_at: artifact.created_at,
      run_ref: artifact.run_id ? runRef(artifact.run_id) : null,
    })),
    decisions: decisions.map((decision) => ({
      id: decision.id,
      summary: decision.summary,
      rationale: decision.rationale,
      decided_at: decision.decided_at,
      actor:
        decision.actor_id ? actorsById.get(decision.actor_id)?.handle ?? null : null,
      run_ref: decision.run_id ? runRef(decision.run_id) : null,
    })),
    activity: buildActivityEntries(task, runs, decisions, artifacts),
    parent_ref:
      relations?.parentId && tasksById.has(relations.parentId)
        ? taskRef(tasksById.get(relations.parentId)!)
        : null,
    dependency_refs: relations
      ? uniqueRefs(
          [
            relations.parentId,
            ...relations.dependencyIds,
            ...relations.blockedByIds,
          ],
          tasksById,
        )
      : [],
    blocked_by_refs: relations ? uniqueRefs([...relations.blockedByIds], tasksById) : [],
    block_refs: relations ? uniqueRefs([...relations.blockIds], tasksById) : [],
    child_refs: relations ? uniqueRefs([...relations.childIds], tasksById) : [],
  };
}

function buildActivityEntries(
  task: TaskEntity,
  runs: RunEntity[],
  decisions: DecisionEntity[],
  artifacts: ArtifactEntity[],
): WorkActivityEntry[] {
  const entries: WorkActivityEntry[] = [
    {
      id: `${task.id}:created`,
      label: "Task created",
      summary: task.description?.trim() || "Initial task contract recorded.",
      happened_at: task.created_at,
      state: "queued",
      source: "task",
      command: null,
    },
  ];

  if (task.claimed_at) {
    entries.push({
      id: `${task.id}:claimed`,
      label: "Task claimed",
      summary: `Task entered ${mapTaskState(task.state)} flow.`,
      happened_at: task.claimed_at,
      state: mapTaskState(task.state),
      source: "task",
      command: null,
    });
  }

  for (const run of runs) {
    const transitions = extractTransitions(run.details);
    const command = extractCommand(run.details);

    if (transitions.length > 0) {
      transitions.forEach((transition, index) => {
        entries.push({
          id: `${run.id}:transition:${index}`,
          label: `${transition.from_state} -> ${transition.to_state}`,
          summary: transition.note ?? run.summary ?? "Recorded task transition.",
          happened_at: run.ended_at ?? run.started_at,
          state: mapTaskState(transition.to_state),
          source: "run",
          command,
        });
      });
      continue;
    }

    entries.push({
      id: `${run.id}:run`,
      label: "Run recorded",
      summary: run.summary ?? "Run details captured for this task.",
      happened_at: run.ended_at ?? run.started_at,
      state: mapTaskState(run.status),
      source: "run",
      command,
    });
  }

  for (const decision of decisions) {
    entries.push({
      id: `${decision.id}:decision`,
      label: "Decision recorded",
      summary: decision.summary,
      happened_at: decision.decided_at,
      state: null,
      source: "decision",
      command: null,
    });
  }

  for (const artifact of artifacts) {
    entries.push({
      id: `${artifact.id}:artifact`,
      label: `${artifact.kind} artifact`,
      summary: artifactLabel(artifact),
      happened_at: artifact.created_at,
      state: null,
      source: "artifact",
      command: null,
    });
  }

  return entries.sort((left, right) => right.happened_at.localeCompare(left.happened_at));
}

function extractTransitions(details: Record<string, unknown>) {
  const rawTransitions = details.transitions;
  if (!Array.isArray(rawTransitions)) {
    return [];
  }

  return rawTransitions.flatMap((entry) => {
    if (!entry || typeof entry !== "object") {
      return [];
    }

    const fromState = readString(entry, "from_state");
    const toState =
      readString(entry, "to_state") ??
      readString(entry, "requested_state") ??
      readString(entry, "state");

    if (!fromState || !toState) {
      return [];
    }

    return [
      {
        from_state: fromState,
        to_state: toState,
        note: readString(entry, "note"),
      },
    ];
  });
}

function readString(value: unknown, key: string) {
  if (!value || typeof value !== "object") {
    return null;
  }

  const entry = (value as Record<string, unknown>)[key];
  return typeof entry === "string" && entry.trim() ? entry : null;
}

function extractCommand(value: unknown): string | null {
  const queue: unknown[] = [value];
  const seen = new Set<unknown>();

  while (queue.length > 0) {
    const current = queue.shift();
    if (current === undefined || seen.has(current)) {
      continue;
    }
    seen.add(current);

    if (typeof current === "string" && looksLikeCommand(current)) {
      return current.trim();
    }

    if (Array.isArray(current)) {
      queue.push(...current);
      continue;
    }

    if (current && typeof current === "object") {
      const record = current as Record<string, unknown>;
      for (const [key, child] of Object.entries(record)) {
        if (
          typeof child === "string" &&
          (key.toLowerCase().includes("command") ||
            key.toLowerCase().includes("cli") ||
            key.toLowerCase().includes("mcp")) &&
          child.trim()
        ) {
          return child.trim();
        }
        queue.push(child);
      }
    }
  }

  return null;
}

function looksLikeCommand(value: string) {
  const normalized = value.trim();
  return /^(aq|gh|docker|npm|npx|pnpm|python|uv|plane_|mcp__)/i.test(normalized);
}

function deriveClosedAt(task: TaskEntity, runs: RunEntity[]) {
  if (!isClosedState(task.state)) {
    return null;
  }

  return runs.find((run) => run.ended_at)?.ended_at ?? task.updated_at;
}

function isClosedState(state: string) {
  return state === "done" || state === "rejected" || state === "dlq";
}

function artifactLabel(artifact: ArtifactEntity) {
  const segments = artifact.uri.split("/");
  const lastSegment = segments[segments.length - 1];
  return lastSegment && lastSegment.length > 0 ? lastSegment : artifact.uri;
}

function runRef(runId: string) {
  return `run-${runId.slice(0, 8)}`;
}

function taskRef(task: TaskEntity) {
  return task.sequence === null ? `job-${task.id.slice(0, 8)}` : `job-${task.sequence}`;
}

function uniqueRefs(taskIds: Array<string | null>, tasksById: Map<string, TaskEntity>) {
  const refs = new Set<string>();
  for (const taskId of taskIds) {
    if (!taskId) {
      continue;
    }
    const task = tasksById.get(taskId);
    if (task) {
      refs.add(taskRef(task));
    }
  }
  return [...refs].sort(compareRefs);
}

function compareRefs(left: string, right: string) {
  const leftNumber = Number.parseInt(left.replace(/^\D+/g, ""), 10);
  const rightNumber = Number.parseInt(right.replace(/^\D+/g, ""), 10);

  if (Number.isFinite(leftNumber) && Number.isFinite(rightNumber) && leftNumber !== rightNumber) {
    return leftNumber - rightNumber;
  }

  return left.localeCompare(right);
}

function compareWorkItems(left: WorkItem, right: WorkItem) {
  const priorityByStatus: Record<WorkStatus, number> = {
    running: 0,
    failed: 1,
    review: 2,
    blocked: 3,
    queued: 4,
    done: 5,
  };

  const statusDelta = priorityByStatus[left.status] - priorityByStatus[right.status];
  if (statusDelta !== 0) {
    return statusDelta;
  }

  if (left.priority !== right.priority) {
    return right.priority - left.priority;
  }

  return right.updated_at.localeCompare(left.updated_at);
}

function groupBy<T>(items: T[], keyFn: (item: T) => string) {
  const grouped = new Map<string, T[]>();
  for (const item of items) {
    const key = keyFn(item);
    const bucket = grouped.get(key);
    if (bucket) {
      bucket.push(item);
    } else {
      grouped.set(key, [item]);
    }
  }
  return grouped;
}

function edgeIsActive(edge: EdgeEntity) {
  const metadata = edge.metadata ?? {};
  return !(
    metadata.superseded_at ||
    metadata.superseded_by ||
    metadata.status === "superseded" ||
    metadata.is_active === false
  );
}

function mapTaskState(state: string): WorkStatus {
  if (state === "done") {
    return "done";
  }
  if (state === "rejected" || state === "dlq") {
    return "failed";
  }
  if (state === "submitted" || state === "validated" || state === "needs_ghost_triage") {
    return "review";
  }
  if (state === "blocked" || state === "parked") {
    return "blocked";
  }
  if (state === "claimed" || state === "in_progress") {
    return "running";
  }
  return "queued";
}
