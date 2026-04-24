import { NextRequest, NextResponse } from "next/server";

type PipelinesResponse = {
  count: number;
  pipelines: unknown[];
};

type WorkItem = {
  ref: string;
};

type WorkResponse = {
  count: number;
  items: WorkItem[];
};

type AnalyticsResponse = {
  cycle_time: unknown[];
  blocked_heatmap: unknown[];
  handoff_latency_histogram: unknown[];
  handoff_latency_by_actor: unknown[];
  retrieval_precision: {
    sample_size: number;
  };
  agent_success_rates: unknown[];
  review_load: Array<{
    count: number;
  }>;
};

type DecisionItem = {
  primary_job_ref: string | null;
  linked_job_refs: string[];
};

type DecisionsResponse = {
  count: number;
  items: DecisionItem[];
};

type LearningsResponse = {
  count: number;
  items: unknown[];
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
    const baseUrl = request.nextUrl.origin;
    const [inProgress, done, work, analytics, decisions, learnings] =
      await Promise.all([
        fetchJson<PipelinesResponse>({
          authorization,
          request,
          url: new URL("/api/v1/pipelines?state=in_progress", baseUrl),
        }),
        fetchJson<PipelinesResponse>({
          authorization,
          request,
          url: new URL("/api/v1/pipelines?state=done", baseUrl),
        }),
        fetchJson<WorkResponse>({
          authorization,
          request,
          url: new URL("/api/v1/work", baseUrl),
        }),
        fetchJson<AnalyticsResponse>({
          authorization,
          request,
          url: new URL("/api/v1/analytics/metrics?window=90d", baseUrl),
        }),
        fetchJson<DecisionsResponse>({
          authorization,
          request,
          url: new URL("/api/v1/decisions", baseUrl),
        }),
        fetchJson<LearningsResponse>({
          authorization,
          request,
          url: new URL("/api/v1/learnings/search?query=", baseUrl),
        }),
      ]);

    return NextResponse.json({
      pipelines: inProgress.count + done.count,
      work: work.count,
      analytics: buildAnalyticsCount(analytics),
      graph: buildGraphCount(work, decisions),
      decisions: decisions.count,
      learnings: learnings.count,
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
            : "Failed to build nav counts.",
      },
      { status: 500 },
    );
  }
}

async function fetchJson<T>({
  authorization,
  request,
  url,
}: {
  authorization: string;
  request: NextRequest;
  url: URL;
}): Promise<T> {
  const response = await fetch(url, {
    headers: {
      Authorization: authorization,
    },
    cache: "no-store",
    signal: request.signal,
  });

  const payload = (await response.json().catch(() => null)) as
    | T
    | { error?: string; message?: string }
    | null;

  if (!response.ok || payload === null) {
    const errorPayload =
      payload && typeof payload === "object" && !Array.isArray(payload)
        ? (payload as { error?: string; message?: string })
        : null;
    const detail =
      errorPayload?.error ?? errorPayload?.message ?? null;
    throw new UpstreamError(
      detail ?? `Upstream request failed for ${url.pathname}.`,
      response.status || 500,
    );
  }

  return payload as T;
}

function buildAnalyticsCount(analytics: AnalyticsResponse) {
  return [
    analytics.cycle_time.length > 0,
    analytics.blocked_heatmap.length > 0,
    analytics.handoff_latency_histogram.length > 0 ||
      analytics.handoff_latency_by_actor.length > 0,
    analytics.retrieval_precision.sample_size > 0,
    analytics.agent_success_rates.length > 0,
    analytics.review_load.some((point) => point.count > 0),
  ].filter(Boolean).length;
}

function buildGraphCount(work: WorkResponse, decisions: DecisionsResponse) {
  const workRefs = new Set(work.items.map((item) => item.ref));
  const linkedJobRefs = new Set<string>();

  for (const item of decisions.items) {
    if (item.primary_job_ref && workRefs.has(item.primary_job_ref)) {
      linkedJobRefs.add(item.primary_job_ref);
    }

    for (const ref of item.linked_job_refs) {
      if (workRefs.has(ref)) {
        linkedJobRefs.add(ref);
      }
    }
  }

  return decisions.count + linkedJobRefs.size;
}
