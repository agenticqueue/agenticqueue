import { API_BASE_URL } from "../../_upstream";

const PAGE_LIMIT = 200;

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

type DecisionDataset = {
  actors: ActorEntity[];
  projects: ProjectEntity[];
  tasks: TaskEntity[];
  decisions: DecisionEntity[];
  edges: EdgeEntity[];
};

type DecisionAdjacency = {
  taskIds: Set<string>;
  directProject: boolean;
  directWorkspace: boolean;
  supersedes: Set<string>;
  supersededBy: Set<string>;
  contradicts: Set<string>;
};

type DecisionIndex = {
  items: DecisionListItem[];
  itemsById: Map<string, DecisionListItem>;
  adjacencyById: Map<string, DecisionAdjacency>;
};

export type DecisionScope = "global" | "project" | "task";
export type DecisionStatus = "active" | "superseded";

export type DecisionListItem = {
  id: string;
  ref: string;
  title: string;
  scope: DecisionScope;
  actor: string | null;
  decided_at: string;
  status: DecisionStatus;
  rationale: string | null;
  project_name: string | null;
  project_slug: string | null;
  primary_job_ref: string | null;
  linked_job_refs: string[];
  supersedes_refs: string[];
  superseded_by_refs: string[];
  alternative_refs: Array<{
    id: string;
    ref: string;
    title: string;
  }>;
};

export type DecisionListResponse = {
  generated_at: string;
  count: number;
  items: DecisionListItem[];
};

export type DecisionLineageNode = {
  id: string;
  ref: string;
  title: string;
  decided_at: string;
  status: DecisionStatus;
  scope: DecisionScope;
  relation: "selected" | "newer" | "older";
  depth: number;
};

export type DecisionLineageEdge = {
  from_id: string;
  to_id: string;
  from_ref: string;
  to_ref: string;
};

export type DecisionLineageResponse = {
  generated_at: string;
  decision_id: string;
  nodes: DecisionLineageNode[];
  edges: DecisionLineageEdge[];
};

export class UpstreamError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "UpstreamError";
    this.status = status;
  }
}

export async function loadDecisionDataset({
  authHeaders,
  signal,
}: {
  authHeaders: Headers;
  signal: AbortSignal;
}): Promise<DecisionDataset> {
  const [actors, projects, tasks, decisions, edges] = await Promise.all([
    fetchAllPages<ActorEntity>({
      path: "/v1/actors",
      authHeaders,
      signal,
    }),
    fetchAllPages<ProjectEntity>({
      path: "/v1/projects",
      authHeaders,
      signal,
    }),
    fetchAllPages<TaskEntity>({
      path: "/v1/tasks",
      authHeaders,
      signal,
    }),
    fetchAllPages<DecisionEntity>({
      path: "/v1/decisions",
      authHeaders,
      signal,
    }),
    fetchAllPages<EdgeEntity>({
      path: "/v1/edges",
      authHeaders,
      signal,
    }),
  ]);

  return {
    actors,
    projects,
    tasks,
    decisions,
    edges: edges.filter(edgeIsActive),
  };
}

export function buildDecisionItems(dataset: DecisionDataset): DecisionListItem[] {
  return buildDecisionIndex(dataset).items;
}

export function buildDecisionLineage(
  decisionId: string,
  dataset: DecisionDataset,
): DecisionLineageResponse | null {
  const index = buildDecisionIndex(dataset);
  if (!index.itemsById.has(decisionId)) {
    return null;
  }

  const nodes: DecisionLineageNode[] = [];
  const edges: DecisionLineageEdge[] = [];
  const seenNodeIds = new Set<string>();
  const seenEdgeKeys = new Set<string>();
  const queue: Array<{ id: string; depth: number }> = [{ id: decisionId, depth: 0 }];

  while (queue.length > 0) {
    const current = queue.shift();
    if (!current || seenNodeIds.has(current.id)) {
      continue;
    }

    seenNodeIds.add(current.id);
    const item = index.itemsById.get(current.id);
    if (!item) {
      continue;
    }

    nodes.push({
      id: item.id,
      ref: item.ref,
      title: item.title,
      decided_at: item.decided_at,
      status: item.status,
      scope: item.scope,
      relation:
        current.depth === 0 ? "selected" : current.depth < 0 ? "newer" : "older",
      depth: current.depth,
    });

    const adjacency = index.adjacencyById.get(current.id);
    if (!adjacency) {
      continue;
    }

    for (const newerId of adjacency.supersededBy) {
      const newerItem = index.itemsById.get(newerId);
      if (!newerItem) {
        continue;
      }

      const edgeKey = `${newerId}->${current.id}`;
      if (!seenEdgeKeys.has(edgeKey)) {
        edges.push({
          from_id: newerId,
          to_id: current.id,
          from_ref: newerItem.ref,
          to_ref: item.ref,
        });
        seenEdgeKeys.add(edgeKey);
      }

      if (!seenNodeIds.has(newerId)) {
        queue.push({ id: newerId, depth: current.depth - 1 });
      }
    }

    for (const olderId of adjacency.supersedes) {
      const olderItem = index.itemsById.get(olderId);
      if (!olderItem) {
        continue;
      }

      const edgeKey = `${current.id}->${olderId}`;
      if (!seenEdgeKeys.has(edgeKey)) {
        edges.push({
          from_id: current.id,
          to_id: olderId,
          from_ref: item.ref,
          to_ref: olderItem.ref,
        });
        seenEdgeKeys.add(edgeKey);
      }

      if (!seenNodeIds.has(olderId)) {
        queue.push({ id: olderId, depth: current.depth + 1 });
      }
    }
  }

  nodes.sort((left, right) => {
    if (left.depth !== right.depth) {
      return left.depth - right.depth;
    }

    return right.decided_at.localeCompare(left.decided_at);
  });

  return {
    generated_at: new Date().toISOString(),
    decision_id: decisionId,
    nodes,
    edges,
  };
}

async function fetchAllPages<T>({
  path,
  authHeaders,
  signal,
}: {
  path: string;
  authHeaders: Headers;
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
      headers: authHeaders,
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

function buildDecisionIndex(dataset: DecisionDataset): DecisionIndex {
  const actorsById = new Map(dataset.actors.map((actor) => [actor.id, actor]));
  const projectsById = new Map(
    dataset.projects.map((project) => [project.id, project]),
  );
  const tasksById = new Map(dataset.tasks.map((task) => [task.id, task]));
  const decisionsById = new Map(
    dataset.decisions.map((decision) => [decision.id, decision]),
  );
  const adjacencyById = new Map<string, DecisionAdjacency>();

  for (const decision of dataset.decisions) {
    adjacencyById.set(decision.id, {
      taskIds: new Set([decision.task_id]),
      directProject: false,
      directWorkspace: false,
      supersedes: new Set<string>(),
      supersededBy: new Set<string>(),
      contradicts: new Set<string>(),
    });
  }

  for (const edge of dataset.edges) {
    if (
      edge.relation === "supersedes" &&
      edge.src_entity_type === "decision" &&
      edge.dst_entity_type === "decision"
    ) {
      adjacencyById.get(edge.src_id)?.supersedes.add(edge.dst_id);
      adjacencyById.get(edge.dst_id)?.supersededBy.add(edge.src_id);
    }

    if (
      edge.relation === "contradicts" &&
      edge.src_entity_type === "decision" &&
      edge.dst_entity_type === "decision"
    ) {
      adjacencyById.get(edge.src_id)?.contradicts.add(edge.dst_id);
      adjacencyById.get(edge.dst_id)?.contradicts.add(edge.src_id);
    }

    if (edge.src_entity_type === "decision") {
      attachDecisionRelation(adjacencyById.get(edge.src_id), edge.dst_entity_type, edge.dst_id);
    }

    if (edge.dst_entity_type === "decision") {
      attachDecisionRelation(adjacencyById.get(edge.dst_id), edge.src_entity_type, edge.src_id);
    }
  }

  const items = dataset.decisions
    .map((decision) => {
      const adjacency = adjacencyById.get(decision.id);
      const linkedTasks = [...(adjacency?.taskIds ?? new Set<string>())]
        .map((taskId) => tasksById.get(taskId))
        .filter((task): task is TaskEntity => Boolean(task))
        .sort(compareTasks);
      const primaryTask = linkedTasks[0] ?? null;
      const primaryProject = primaryTask
        ? projectsById.get(primaryTask.project_id) ?? null
        : null;
      const supersedesRefs = [...(adjacency?.supersedes ?? new Set<string>())]
        .map((id) => decisionsById.get(id))
        .filter((entry): entry is DecisionEntity => Boolean(entry))
        .sort((left, right) => right.decided_at.localeCompare(left.decided_at))
        .map((entry) => decisionRef(entry.id));
      const supersededByRefs = [...(adjacency?.supersededBy ?? new Set<string>())]
        .map((id) => decisionsById.get(id))
        .filter((entry): entry is DecisionEntity => Boolean(entry))
        .sort((left, right) => right.decided_at.localeCompare(left.decided_at))
        .map((entry) => decisionRef(entry.id));
      const alternativeRefs = [...(adjacency?.contradicts ?? new Set<string>())]
        .map((id) => decisionsById.get(id))
        .filter((entry): entry is DecisionEntity => Boolean(entry))
        .sort((left, right) => right.decided_at.localeCompare(left.decided_at))
        .map((entry) => ({
          id: entry.id,
          ref: decisionRef(entry.id),
          title: entry.summary,
        }));

      return {
        id: decision.id,
        ref: decisionRef(decision.id),
        title: decision.summary,
        scope: deriveDecisionScope(adjacency),
        actor: decision.actor_id
          ? actorsById.get(decision.actor_id)?.handle ?? null
          : null,
        decided_at: decision.decided_at,
        status:
          adjacency && adjacency.supersededBy.size > 0 ? "superseded" : "active",
        rationale: decision.rationale,
        project_name: primaryProject?.name ?? null,
        project_slug: primaryProject?.slug ?? null,
        primary_job_ref: primaryTask ? taskRef(primaryTask) : null,
        linked_job_refs: linkedTasks.map((task) => taskRef(task)),
        supersedes_refs: supersedesRefs,
        superseded_by_refs: supersededByRefs,
        alternative_refs: alternativeRefs,
      } satisfies DecisionListItem;
    })
    .sort(compareDecisionItems);

  return {
    items,
    itemsById: new Map(items.map((item) => [item.id, item])),
    adjacencyById,
  };
}

function attachDecisionRelation(
  adjacency: DecisionAdjacency | undefined,
  entityType: string,
  entityId: string,
) {
  if (!adjacency) {
    return;
  }

  if (entityType === "task") {
    adjacency.taskIds.add(entityId);
    return;
  }

  if (entityType === "project") {
    adjacency.directProject = true;
    return;
  }

  if (entityType === "workspace") {
    adjacency.directWorkspace = true;
  }
}

function deriveDecisionScope(
  adjacency: DecisionAdjacency | undefined,
): DecisionScope {
  if (!adjacency) {
    return "project";
  }

  if (adjacency.directWorkspace) {
    return "global";
  }

  if (adjacency.directProject || adjacency.taskIds.size !== 1) {
    return "project";
  }

  return "task";
}

function decisionRef(id: string) {
  return `dc-${id.slice(0, 8)}`;
}

function taskRef(task: TaskEntity) {
  return task.sequence === null ? `job-${task.id.slice(0, 8)}` : `job-${task.sequence}`;
}

function compareTasks(left: TaskEntity, right: TaskEntity) {
  return compareRefs(taskRef(left), taskRef(right));
}

function compareDecisionItems(left: DecisionListItem, right: DecisionListItem) {
  const decidedDelta = right.decided_at.localeCompare(left.decided_at);
  if (decidedDelta !== 0) {
    return decidedDelta;
  }

  return left.ref.localeCompare(right.ref);
}

function compareRefs(left: string, right: string) {
  const leftNumber = Number.parseInt(left.replace(/^\D+/g, ""), 10);
  const rightNumber = Number.parseInt(right.replace(/^\D+/g, ""), 10);

  if (
    Number.isFinite(leftNumber) &&
    Number.isFinite(rightNumber) &&
    leftNumber !== rightNumber
  ) {
    return leftNumber - rightNumber;
  }

  return left.localeCompare(right);
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
