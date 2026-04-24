import { NextRequest, NextResponse } from "next/server";

import { getApiBaseUrl } from "@/lib/api-base-url";

const API_BASE_URL = getApiBaseUrl();

const PAGE_LIMIT = 200;

type LearningScope = "task" | "project" | "global";
type LearningStatus = "active" | "superseded" | "expired";

type LearningEntity = {
  id: string;
  task_id: string | null;
  owner_actor_id: string | null;
  owner: string | null;
  title: string;
  learning_type: string;
  what_happened: string;
  what_learned: string;
  action_rule: string;
  applies_when: string;
  does_not_apply_when: string;
  evidence: string[];
  scope: LearningScope;
  promotion_eligible: boolean;
  confidence: string;
  status: LearningStatus;
  review_date: string | null;
  created_at: string;
  updated_at: string;
};

type TaskEntity = {
  id: string;
  title: string;
  sequence: number | null;
  updated_at: string;
};

type LearningSearchItem = {
  id: string;
  ref: string;
  title: string;
  scope: LearningScope;
  tier: 1 | 2 | 3;
  confidence: string;
  status: LearningStatus;
  last_applied: string;
  owner: string | null;
  review_date: string | null;
  context: {
    what_happened: string;
    what_learned: string;
    action_rule: string;
    applies_when: string;
    does_not_apply_when: string;
  };
  evidence: string[];
  applied_in: Array<{
    task_id: string;
    ref: string;
    title: string;
    href: string;
  }>;
};

type LearningSearchResponse = {
  query: string;
  count: number;
  generated_at: string;
  items: LearningSearchItem[];
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

  const query = request.nextUrl.searchParams.get("query")?.trim() ?? "";

  try {
    const [active, superseded, expired, tasks] = await Promise.all([
      fetchAllPages<LearningEntity>({
        authorization,
        path: "/v1/learnings",
        query: { status: "active" },
        signal: request.signal,
      }),
      fetchAllPages<LearningEntity>({
        authorization,
        path: "/v1/learnings",
        query: { status: "superseded" },
        signal: request.signal,
      }),
      fetchAllPages<LearningEntity>({
        authorization,
        path: "/v1/learnings",
        query: { status: "expired" },
        signal: request.signal,
      }),
      fetchAllPages<TaskEntity>({
        authorization,
        path: "/v1/tasks",
        signal: request.signal,
      }),
    ]);

    const items = buildLearningItems(
      [...active, ...superseded, ...expired],
      tasks,
      query,
    );

    return NextResponse.json<LearningSearchResponse>({
      query,
      count: items.length,
      generated_at: new Date().toISOString(),
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
            : "Failed to load learnings.",
      },
      { status: 500 },
    );
  }
}

async function fetchAllPages<T>({
  authorization,
  path,
  query,
  signal,
}: {
  authorization: string;
  path: string;
  query?: Record<string, string>;
  signal: AbortSignal;
}): Promise<T[]> {
  const items: T[] = [];
  let cursor: string | null = null;

  do {
    const url = new URL(path, API_BASE_URL);
    url.searchParams.set("limit", String(PAGE_LIMIT));
    if (query) {
      for (const [key, value] of Object.entries(query)) {
        url.searchParams.set(key, value);
      }
    }
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
        payload && !Array.isArray(payload)
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

function buildLearningItems(
  learnings: LearningEntity[],
  tasks: TaskEntity[],
  query: string,
): LearningSearchItem[] {
  const tasksById = new Map(tasks.map((task) => [task.id, task]));
  const normalizedQuery = query.trim().toLowerCase();
  const deduped = new Map(learnings.map((learning) => [learning.id, learning]));

  return [...deduped.values()]
    .map((learning) => serializeLearning(learning, tasksById))
    .filter((item) => matchesQuery(item, normalizedQuery))
    .sort(compareLearningItems);
}

function serializeLearning(
  learning: LearningEntity,
  tasksById: Map<string, TaskEntity>,
): LearningSearchItem {
  const task = learning.task_id ? tasksById.get(learning.task_id) ?? null : null;
  const taskReference = task ? taskRef(task) : null;

  return {
    id: learning.id,
    ref: `lrn-${learning.id.slice(0, 8)}`,
    title: learning.title,
    scope: learning.scope,
    tier: scopeTier(learning.scope),
    confidence: learning.confidence,
    status: learning.status,
    last_applied: task?.updated_at ?? learning.updated_at,
    owner: learning.owner,
    review_date: learning.review_date,
    context: {
      what_happened: learning.what_happened,
      what_learned: learning.what_learned,
      action_rule: learning.action_rule,
      applies_when: learning.applies_when,
      does_not_apply_when: learning.does_not_apply_when,
    },
    evidence: learning.evidence,
    applied_in:
      task && taskReference
        ? [
            {
              task_id: task.id,
              ref: taskReference,
              title: task.title,
              href: `/work?job=${encodeURIComponent(taskReference)}`,
            },
          ]
        : [],
  };
}

function matchesQuery(item: LearningSearchItem, query: string) {
  if (!query) {
    return true;
  }

  const haystack = [
    item.ref,
    item.title,
    item.scope,
    item.status,
    item.confidence,
    item.owner ?? "",
    item.context.what_happened,
    item.context.what_learned,
    item.context.action_rule,
    item.context.applies_when,
    item.context.does_not_apply_when,
    ...item.evidence,
    ...item.applied_in.flatMap((entry) => [entry.ref, entry.title]),
  ]
    .join("\n")
    .toLowerCase();

  return haystack.includes(query);
}

function scopeTier(scope: LearningScope): 1 | 2 | 3 {
  if (scope === "project") {
    return 2;
  }
  if (scope === "global") {
    return 3;
  }
  return 1;
}

function taskRef(task: TaskEntity) {
  return task.sequence === null ? `job-${task.id.slice(0, 8)}` : `job-${task.sequence}`;
}

function compareLearningItems(left: LearningSearchItem, right: LearningSearchItem) {
  const statusOrder: Record<LearningStatus, number> = {
    active: 0,
    superseded: 1,
    expired: 2,
  };
  const statusDelta = statusOrder[left.status] - statusOrder[right.status];
  if (statusDelta !== 0) {
    return statusDelta;
  }
  return right.last_applied.localeCompare(left.last_applied);
}
