import { NextRequest, NextResponse } from "next/server";

const API_BASE_URL =
  process.env.AGENTICQUEUE_API_BASE_URL ??
  process.env.NEXT_PUBLIC_AGENTICQUEUE_API_BASE_URL ??
  "http://127.0.0.1:8010";

const PAGE_LIMIT = 200;

type PipelineSectionState = "in_progress" | "done";
type PipelineJobState =
  | "running"
  | "failed"
  | "review"
  | "queued"
  | "blocked"
  | "done";
type PipelineTone = "ok" | "info" | "warn" | "danger" | "mute";

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
  created_at: string;
  updated_at: string;
};

type PolicyEntity = {
  id: string;
  workspace_id: string | null;
  name: string;
  version: string;
  hitl_required: boolean;
  autonomy_tier: number;
  capabilities: string[];
  body: Record<string, unknown>;
  created_at: string;
  updated_at: string;
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
};

type PipelineRelation = {
  id: string;
  ref: string;
  title: string;
  status: PipelineJobState;
  raw_state: string;
};

type PipelineJob = {
  id: string;
  ref: string;
  title: string;
  task_type: string;
  status: PipelineJobState;
  raw_state: string;
  priority: number;
  labels: string[];
  sequence: number | null;
  description: string | null;
  claimed_by_actor_id: string | null;
  claimed_at: string | null;
  created_at: string;
  updated_at: string;
  parent_ref: string | null;
  dependency_refs: string[];
  child_refs: string[];
  depends_on: PipelineRelation[];
  blocked_by: PipelineRelation[];
  blocks: PipelineRelation[];
};

type PipelineSummary = {
  id: string;
  slug: string;
  name: string;
  goal: string;
  state: PipelineSectionState;
  tone: PipelineTone;
  progress: {
    done: number;
    total: number;
  };
  autonomy: {
    label: string;
    tone: PipelineTone;
  };
  attention: {
    failed: number;
    needs_review: number;
    running: number;
    queued: number;
    blocked: number;
  };
  started_at: string | null;
  updated_at: string;
  completed_at: string | null;
  tasks: PipelineJob[];
};

type PipelinesResponse = {
  state: PipelineSectionState;
  count: number;
  generated_at: string;
  pipelines: PipelineSummary[];
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
  const requestedState = request.nextUrl.searchParams.get("state");
  if (requestedState !== "in_progress" && requestedState !== "done") {
    return NextResponse.json(
      { error: "Query parameter state must be in_progress or done." },
      { status: 400 },
    );
  }

  const authorization = request.headers.get("authorization")?.trim();
  if (!authorization) {
    return NextResponse.json(
      { error: "Authorization header is required." },
      { status: 401 },
    );
  }

  try {
    const [projects, tasks, policies, edges] = await Promise.all([
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
      fetchAllPages<PolicyEntity>({
        path: "/v1/policies",
        authorization,
        signal: request.signal,
      }),
      fetchAllPages<EdgeEntity>({
        path: "/v1/edges",
        authorization,
        signal: request.signal,
      }),
    ]);

    const pipelines = buildPipelineSummaries(projects, tasks, policies, edges).filter(
      (pipeline) => pipeline.state === requestedState,
    );

    return NextResponse.json<PipelinesResponse>({
      state: requestedState,
      count: pipelines.length,
      generated_at: new Date().toISOString(),
      pipelines,
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
            : "Failed to aggregate pipelines.",
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
      | { message?: string; error?: string }
      | null;

    if (!response.ok || !Array.isArray(payload)) {
      const detail =
        payload && !Array.isArray(payload) && typeof payload === "object"
          ? payload.message ?? payload.error
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

function buildPipelineSummaries(
  projects: ProjectEntity[],
  tasks: TaskEntity[],
  policies: PolicyEntity[],
  edges: EdgeEntity[],
): PipelineSummary[] {
  const tasksByProject = groupBy(tasks, (task) => task.project_id);
  const policiesById = new Map(policies.map((policy) => [policy.id, policy]));

  return [...projects]
    .map((project) =>
      buildPipelineSummary(
        project,
        tasksByProject.get(project.id) ?? [],
        policiesById,
        edges,
      ),
    )
    .sort(comparePipelines);
}

function buildPipelineSummary(
  project: ProjectEntity,
  projectTasks: TaskEntity[],
  policiesById: Map<string, PolicyEntity>,
  edges: EdgeEntity[],
): PipelineSummary {
  const sortedTasks = [...projectTasks].sort(compareTasks);
  const taskIdSet = new Set(sortedTasks.map((task) => task.id));
  const tasksById = new Map(sortedTasks.map((task) => [task.id, task]));
  const activeEdges = edges.filter(
    (edge) =>
      edgeIsActive(edge) &&
      edge.src_entity_type === "task" &&
      edge.dst_entity_type === "task" &&
      taskIdSet.has(edge.src_id) &&
      taskIdSet.has(edge.dst_id),
  );
  const relationsByTaskId = new Map<string, RelationAccumulator>(
    sortedTasks.map((task) => [
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

  const jobs = sortedTasks.map((task) =>
    serializeTask(task, relationsByTaskId, tasksById),
  );
  const doneCount = sortedTasks.filter((task) => task.state === "done").length;
  const attention = {
    failed: sortedTasks.filter((task) => isFailureState(task.state)).length,
    needs_review: sortedTasks.filter((task) => isNeedsReviewState(task.state))
      .length,
    running: sortedTasks.filter((task) => isRunningState(task.state)).length,
    queued: sortedTasks.filter((task) => task.state === "queued").length,
    blocked: sortedTasks.filter((task) => isBlockedState(task.state)).length,
  };
  const state: PipelineSectionState =
    sortedTasks.length > 0 && doneCount === sortedTasks.length
      ? "done"
      : "in_progress";
  const tone = derivePipelineTone(state, attention);
  const autonomy = deriveAutonomy(project, sortedTasks, policiesById);
  const updatedAt = latestTimestamp(
    [project.updated_at, ...sortedTasks.map((task) => task.updated_at)].filter(
      isDefinedString,
    ),
  );
  const startedAt = earliestTimestamp(
    sortedTasks
      .flatMap((task) => [task.claimed_at, task.created_at])
      .filter(isDefinedString),
  );
  const completedAt =
    state === "done"
      ? latestTimestamp(sortedTasks.map((task) => task.updated_at))
      : null;

  return {
    id: project.id,
    slug: project.slug,
    name: project.name,
    goal: project.description?.trim() || "No pipeline goal recorded yet.",
    state,
    tone,
    progress: {
      done: doneCount,
      total: sortedTasks.length,
    },
    autonomy,
    attention,
    started_at: startedAt,
    updated_at: updatedAt ?? project.updated_at,
    completed_at: completedAt,
    tasks: jobs,
  };
}

function serializeTask(
  task: TaskEntity,
  relationsByTaskId: Map<string, RelationAccumulator>,
  tasksById: Map<string, TaskEntity>,
): PipelineJob {
  const relations = relationsByTaskId.get(task.id);

  return {
    id: task.id,
    ref: task.sequence === null ? `job-${task.id.slice(0, 8)}` : `job-${task.sequence}`,
    title: task.title,
    task_type: task.task_type,
    status: mapTaskState(task.state),
    raw_state: task.state,
    priority: task.priority,
    labels: task.labels,
    sequence: task.sequence,
    description: task.description,
    claimed_by_actor_id: task.claimed_by_actor_id,
    claimed_at: task.claimed_at,
    created_at: task.created_at,
    updated_at: task.updated_at,
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
    child_refs: relations ? uniqueRefs([...relations.childIds], tasksById) : [],
    depends_on: relations
      ? serializeRelations([...relations.dependencyIds], tasksById)
      : [],
    blocked_by: relations
      ? serializeRelations([...relations.blockedByIds], tasksById)
      : [],
    blocks: relations ? serializeRelations([...relations.blockIds], tasksById) : [],
  };
}

function serializeRelations(
  taskIds: string[],
  tasksById: Map<string, TaskEntity>,
): PipelineRelation[] {
  return taskIds
    .map((taskId) => tasksById.get(taskId))
    .filter((task): task is TaskEntity => Boolean(task))
    .sort(compareTasks)
    .map((task) => ({
      id: task.id,
      ref: taskRef(task),
      title: task.title,
      status: mapTaskState(task.state),
      raw_state: task.state,
    }));
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
  return [...refs];
}

function taskRef(task: TaskEntity) {
  return task.sequence === null ? `job-${task.id.slice(0, 8)}` : `job-${task.sequence}`;
}

function deriveAutonomy(
  project: ProjectEntity,
  projectTasks: TaskEntity[],
  policiesById: Map<string, PolicyEntity>,
): { label: string; tone: PipelineTone } {
  const projectPolicy = project.policy_id ? policiesById.get(project.policy_id) : null;
  const taskPolicies = projectTasks
    .map((task) => (task.policy_id ? policiesById.get(task.policy_id) : null))
    .filter((policy): policy is PolicyEntity => Boolean(policy));
  const strongestPolicy = [projectPolicy, ...taskPolicies]
    .filter((policy): policy is PolicyEntity => Boolean(policy))
    .sort((left, right) => right.autonomy_tier - left.autonomy_tier)[0];
  const contractTier = projectTasks.reduce((highest, task) => {
    const rawTier = task.contract?.autonomy_tier;
    const parsedTier =
      typeof rawTier === "number"
        ? rawTier
        : typeof rawTier === "string"
          ? Number.parseInt(rawTier, 10)
          : Number.NaN;
    return Number.isFinite(parsedTier) ? Math.max(highest, parsedTier) : highest;
  }, 0);

  if (!strongestPolicy && contractTier === 0) {
    return { label: "policy unset", tone: "warn" };
  }

  const tier = Math.max(strongestPolicy?.autonomy_tier ?? 0, contractTier);
  if (strongestPolicy?.hitl_required) {
    return { label: `HITL required · tier ${tier || strongestPolicy.autonomy_tier}`, tone: "warn" };
  }

  if (strongestPolicy) {
    return { label: `Autonomy tier ${tier || strongestPolicy.autonomy_tier}`, tone: "ok" };
  }

  return { label: `Contract tier ${contractTier}`, tone: "info" };
}

function derivePipelineTone(
  state: PipelineSectionState,
  attention: PipelineSummary["attention"],
): PipelineTone {
  if (state === "done") {
    return "ok";
  }
  if (attention.failed > 0) {
    return "danger";
  }
  if (attention.blocked > 0 || attention.needs_review > 0) {
    return "warn";
  }
  if (attention.running > 0) {
    return "info";
  }
  if (attention.queued > 0) {
    return "mute";
  }
  return "info";
}

function mapTaskState(state: string): PipelineJobState {
  if (state === "done") {
    return "done";
  }
  if (isFailureState(state)) {
    return "failed";
  }
  if (isNeedsReviewState(state)) {
    return "review";
  }
  if (isBlockedState(state)) {
    return "blocked";
  }
  if (isRunningState(state)) {
    return "running";
  }
  return "queued";
}

function isFailureState(state: string) {
  return state === "rejected" || state === "dlq";
}

function isNeedsReviewState(state: string) {
  return (
    state === "submitted" ||
    state === "validated" ||
    state === "needs_ghost_triage"
  );
}

function isRunningState(state: string) {
  return state === "claimed" || state === "in_progress";
}

function isBlockedState(state: string) {
  return state === "blocked" || state === "parked";
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

function compareTasks(left: TaskEntity, right: TaskEntity) {
  if (left.sequence !== null && right.sequence !== null && left.sequence !== right.sequence) {
    return left.sequence - right.sequence;
  }
  return left.created_at.localeCompare(right.created_at);
}

function comparePipelines(left: PipelineSummary, right: PipelineSummary) {
  if (left.state !== right.state) {
    return left.state === "in_progress" ? -1 : 1;
  }

  const leftPriority =
    left.attention.failed * 50 +
    left.attention.blocked * 25 +
    left.attention.needs_review * 10 +
    left.attention.running * 5;
  const rightPriority =
    right.attention.failed * 50 +
    right.attention.blocked * 25 +
    right.attention.needs_review * 10 +
    right.attention.running * 5;

  if (leftPriority !== rightPriority) {
    return rightPriority - leftPriority;
  }

  return right.updated_at.localeCompare(left.updated_at);
}

function latestTimestamp(values: string[]) {
  return [...values].sort((left, right) => right.localeCompare(left))[0] ?? null;
}

function earliestTimestamp(values: string[]) {
  return [...values].sort((left, right) => left.localeCompare(right))[0] ?? null;
}

function isDefinedString(value: string | null): value is string {
  return value !== null;
}
