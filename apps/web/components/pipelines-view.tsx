"use client";

import {
  CSSProperties,
  startTransition,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import { JobDetailPanel } from "@/components/job-detail-panel";

type PipelineTone = "ok" | "info" | "warn" | "danger" | "mute";
type PipelineSectionState = "in_progress" | "done";
type PipelineJobState =
  | "running"
  | "failed"
  | "review"
  | "queued"
  | "blocked"
  | "done";

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

type PipelinesViewProps = {
  authToken: string;
};

type SelectedPipelineJob = {
  pipelineId: string;
  ref: string;
};

type JobLayout = {
  columns: Map<number, PipelineJob[]>;
  positions: Map<string, { x: number; y: number }>;
  edges: Array<{
    from: string;
    to: string;
    kind: "dependency" | "parent";
  }>;
  width: number;
  height: number;
};

type CachedPipelinesResponse = {
  expiresAt: number;
  payload: PipelinesResponse;
};

const TONE_ACCENT: Record<PipelineTone, string> = {
  ok: "var(--tone-ok-fg)",
  info: "var(--tone-info-fg)",
  warn: "var(--tone-warn-fg)",
  danger: "var(--tone-danger-fg)",
  mute: "var(--ink-faint)",
};

const JOB_ICON: Record<PipelineJobState, string> = {
  running: "●",
  failed: "×",
  review: "!",
  queued: "○",
  blocked: "⊘",
  done: "✓",
};

const PIPELINES_CACHE_TTL_MS = 10_000;
const PIPELINES_CACHE_MAX_ENTRIES = 16;
const PIPELINE_SKELETON_ROWS = 3;
const pipelinesResponseCache = new Map<string, CachedPipelinesResponse>();

export function PipelinesView({ authToken }: PipelinesViewProps) {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [inProgress, setInProgress] = useState<PipelineSummary[]>([]);
  const [completed, setCompleted] = useState<PipelineSummary[]>([]);
  const [sectionOpen, setSectionOpen] = useState({
    in_progress: true,
    done: false,
  });
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [selectedJob, setSelectedJob] = useState<SelectedPipelineJob | null>(
    null,
  );
  const focusReturnIdRef = useRef<string | null>(null);

  const allPipelines = useMemo(
    () => [...inProgress, ...completed],
    [completed, inProgress],
  );
  const selectedPipeline = selectedJob
    ? allPipelines.find((pipeline) => pipeline.id === selectedJob.pipelineId) ??
      null
    : null;
  const selectedJobDetail =
    selectedPipeline && selectedJob
      ? selectedPipeline.tasks.find((job) => job.ref === selectedJob.ref) ?? null
      : null;

  const closeSelectedJob = useCallback(() => {
    const focusReturnId = focusReturnIdRef.current;
    setSelectedJob(null);

    if (focusReturnId) {
      window.requestAnimationFrame(() => {
        document.getElementById(focusReturnId)?.focus();
      });
    }
  }, []);

  const selectJob = useCallback(
    (pipelineId: string, ref: string, focusReturnId?: string) => {
      if (focusReturnId) {
        focusReturnIdRef.current = focusReturnId;
      }
      setSelectedJob({ pipelineId, ref });
    },
    [],
  );

  const selectRelatedJob = useCallback(
    (ref: string) => {
      if (!selectedPipeline) {
        return;
      }

      if (selectedPipeline.tasks.some((job) => job.ref === ref)) {
        setSelectedJob({ pipelineId: selectedPipeline.id, ref });
      }
    },
    [selectedPipeline],
  );

  useEffect(() => {
    const controller = new AbortController();

    setLoading(true);
    setError(null);

    void Promise.all([
      fetchPipelines("in_progress", authToken, controller.signal),
      fetchPipelines("done", authToken, controller.signal),
    ])
      .then(([inProgressPayload, completedPayload]) => {
        setInProgress(inProgressPayload.pipelines);
        setCompleted(completedPayload.pipelines);
        setExpanded((previous) => {
          if (Object.keys(previous).length > 0) {
            return previous;
          }

          const firstPipeline = inProgressPayload.pipelines[0];
          return firstPipeline ? { [firstPipeline.id]: true } : {};
        });
      })
      .catch((requestError: unknown) => {
        if (controller.signal.aborted) {
          return;
        }

        setError(
          requestError instanceof Error
            ? requestError.message
            : "Failed to load pipelines.",
        );
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setLoading(false);
        }
      });

    return () => controller.abort();
  }, [authToken, reloadKey]);

  useEffect(() => {
    if (selectedJob && !selectedJobDetail) {
      setSelectedJob(null);
    }
  }, [selectedJob, selectedJobDetail]);

  useEffect(() => {
    if (!selectedJob || !selectedPipeline) {
      return;
    }

    const orderedJobs = [...selectedPipeline.tasks].sort(comparePipelineJobs);
    if (orderedJobs.length === 0) {
      return;
    }

    const handleKeydown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        closeSelectedJob();
        return;
      }

      if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") {
        return;
      }

      event.preventDefault();
      const currentIndex = Math.max(
        orderedJobs.findIndex((job) => job.ref === selectedJob.ref),
        0,
      );
      const direction = event.key === "ArrowRight" ? 1 : -1;
      const nextIndex =
        (currentIndex + direction + orderedJobs.length) % orderedJobs.length;
      setSelectedJob({
        pipelineId: selectedPipeline.id,
        ref: orderedJobs[nextIndex].ref,
      });
    };

    document.addEventListener("keydown", handleKeydown);
    return () => document.removeEventListener("keydown", handleKeydown);
  }, [closeSelectedJob, selectedJob, selectedPipeline]);

  const retryLoad = () => {
    setError(null);
    startTransition(() => {
      setLoading(true);
      setReloadKey((current) => current + 1);
    });
  };

  return (
    <div className="aq-pipelines-view">
      <div className="aq-content-head aq-content-head-pipelines">
        <div>
          <p className="aq-content-eyebrow">Phase 7 live view</p>
          <h1 className="aq-content-title">Pipelines</h1>
        </div>
        <p className="aq-content-summary">
          Read-only coordination view over real project, task, policy, and edge
          data. Expand a pipeline to inspect its execution chain inline; use the
          CLI or MCP tools for all writes.
        </p>
      </div>

      <div className="aq-pipelines-readonly">
        <span className="aq-pipelines-readonly-kicker">read-only</span>
        <span>
          Writes stay outside the web shell. Claim, submit, approve, reject, and
          mutate through the API, CLI, or MCP.
        </span>
      </div>

      {loading ? (
        <PipelinesLoadingState />
      ) : error ? (
        <PipelinesErrorState error={error} onRetry={retryLoad} />
      ) : (
        <div className="aq-pipelines-sections">
          <PipelineSection
            defaultOpen={sectionOpen.in_progress}
            emptyCopy="No in-progress pipelines match backend truth."
            onToggle={() =>
              setSectionOpen((previous) => ({
                ...previous,
                in_progress: !previous.in_progress,
              }))
            }
            open={sectionOpen.in_progress}
            pipelines={inProgress}
            section="in_progress"
            title="Pipelines (in progress)"
          />

          {sectionOpen.in_progress ? (
            <div className="aq-pipe-list">
              {inProgress.map((pipeline) => (
                <PipelineCard
                  expanded={Boolean(expanded[pipeline.id])}
                  key={pipeline.id}
                  onToggle={() =>
                    setExpanded((previous) => ({
                      ...previous,
                      [pipeline.id]: !previous[pipeline.id],
                    }))
                  }
                  onJobSelect={(ref, focusReturnId) =>
                    selectJob(pipeline.id, ref, focusReturnId)
                  }
                  pipeline={pipeline}
                  selectedRef={
                    selectedJob?.pipelineId === pipeline.id
                      ? selectedJob.ref
                      : null
                  }
                />
              ))}
            </div>
          ) : null}

          <PipelineSection
            defaultOpen={sectionOpen.done}
            emptyCopy="No completed pipelines yet."
            onToggle={() =>
              setSectionOpen((previous) => ({
                ...previous,
                done: !previous.done,
              }))
            }
            open={sectionOpen.done}
            pipelines={completed}
            section="done"
            title="Pipelines (completed)"
          />

          {sectionOpen.done ? (
            <div className="aq-pipe-list">
              {completed.map((pipeline) => (
                <PipelineCard
                  expanded={Boolean(expanded[pipeline.id])}
                  key={pipeline.id}
                  onToggle={() =>
                    setExpanded((previous) => ({
                      ...previous,
                      [pipeline.id]: !previous[pipeline.id],
                    }))
                  }
                  onJobSelect={(ref, focusReturnId) =>
                    selectJob(pipeline.id, ref, focusReturnId)
                  }
                  pipeline={pipeline}
                  selectedRef={
                    selectedJob?.pipelineId === pipeline.id
                      ? selectedJob.ref
                      : null
                  }
                />
              ))}
            </div>
          ) : null}
        </div>
      )}

      {selectedPipeline && selectedJobDetail ? (
        <JobDetailPanel
          job={selectedJobDetail}
          onClose={closeSelectedJob}
          onSelect={selectRelatedJob}
          open
          pipelineName={selectedPipeline.name}
        />
      ) : null}
    </div>
  );
}

function PipelinesLoadingState() {
  return (
    <div className="aq-pipelines-state" aria-busy="true">
      <div className="aq-state-loader" aria-hidden="true">
        <span className="aq-state-loader-dot" />
        <span className="aq-state-loader-bar" />
      </div>
      <p className="aq-auth-kicker">Loading pipelines</p>
      <h2 className="aq-state-title">Building the live read model</h2>
      <p className="aq-state-copy">
        Paging projects, tasks, policies, and edges through the web proxy.
      </p>

      <div className="aq-pipeline-skeleton-list" aria-hidden="true">
        {Array.from({ length: PIPELINE_SKELETON_ROWS }, (_, index) => (
          <div className="aq-pipeline-skeleton-row" key={index}>
            <span className="aq-pipeline-skeleton-gutter" />
            <span className="aq-pipeline-skeleton-accent" />
            <div className="aq-pipeline-skeleton-main">
              <span className="aq-pipeline-skeleton-line is-title" />
              <span className="aq-pipeline-skeleton-line" />
              <span className="aq-pipeline-skeleton-line is-short" />
            </div>
            <div className="aq-pipeline-skeleton-meta">
              <span className="aq-pipeline-skeleton-pill" />
              <span className="aq-pipeline-skeleton-line is-mini" />
            </div>
            <div className="aq-pipeline-skeleton-chips">
              <span className="aq-pipeline-skeleton-pill" />
              <span className="aq-pipeline-skeleton-pill" />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function PipelinesErrorState({
  error,
  onRetry,
}: {
  error: string;
  onRetry: () => void;
}) {
  return (
    <div className="aq-pipelines-state is-error" role="alert">
      <p className="aq-auth-kicker">Load failure</p>
      <h2 className="aq-state-title">Pipelines could not be loaded</h2>
      <p className="aq-state-copy">
        The live read model could not be refreshed from the web proxy.
      </p>
      <p className="aq-state-detail aq-mono">{error}</p>
      <button
        className="aq-secondary-button aq-state-retry"
        onClick={onRetry}
        type="button"
      >
        Retry load
      </button>
    </div>
  );
}

type PipelineSectionProps = {
  defaultOpen: boolean;
  emptyCopy: string;
  onToggle: () => void;
  open: boolean;
  pipelines: PipelineSummary[];
  section: PipelineSectionState;
  title: string;
};

function PipelineSection({
  emptyCopy,
  onToggle,
  open,
  pipelines,
  section,
  title,
}: PipelineSectionProps) {
  return (
    <section className="aq-pipeline-section">
      <button
        aria-expanded={open}
        className="aq-section-toggle"
        onClick={onToggle}
        type="button"
      >
        <span className="aq-section-left">
          <span className="aq-section-chevron">{open ? "▾" : "▸"}</span>
          <span>{title}</span>
        </span>
        <span className="aq-section-right">
          <span className="aq-tab-count">{pipelines.length}</span>
          <span className="aq-section-hint">
            {section === "done" ? "collapsed by default" : "expanded by default"}
          </span>
        </span>
      </button>
      {open && pipelines.length === 0 ? (
        <div className="aq-empty aq-empty-pipelines">
          <span className="aq-mono aq-mute">{`// ${emptyCopy}`}</span>
        </div>
      ) : null}
    </section>
  );
}

type PipelineCardProps = {
  expanded: boolean;
  onJobSelect: (ref: string, focusReturnId: string) => void;
  onToggle: () => void;
  pipeline: PipelineSummary;
  selectedRef: string | null;
};

function PipelineCard({
  expanded,
  onJobSelect,
  onToggle,
  pipeline,
  selectedRef,
}: PipelineCardProps) {
  const hasTasks = pipeline.tasks.length > 0;
  const rowStyle = {
    "--accent": TONE_ACCENT[pipeline.tone],
  } as CSSProperties;
  const attentionChips = buildAttentionChips(pipeline);

  return (
    <>
      <button
        aria-expanded={expanded}
        className={`aq-pipe-row ${expanded ? "is-open" : ""}`}
        onClick={hasTasks ? onToggle : undefined}
        style={rowStyle}
        type="button"
      >
        <div className="aq-pipe-gutter">
          {hasTasks ? (expanded ? "▾" : "▸") : "·"}
        </div>
        <div className="aq-pipe-accent" />
        <div className="aq-pipe-body">
          <div className="aq-pipe-head">
            <span className="aq-pipe-name">{pipeline.name}</span>
            <span className="aq-pipe-id">{pipeline.slug}</span>
          </div>
          <div className="aq-pipe-desc">{pipeline.goal}</div>
          {pipeline.attention.blocked > 0 ? (
            <div className="aq-pipe-blocked">
              blocked tasks present
              <span className="aq-mono">{pipeline.attention.blocked}</span>
            </div>
          ) : null}
        </div>
        <div className="aq-pipe-meta">
          <ProgressMeter
            done={pipeline.progress.done}
            tone={pipeline.tone}
            total={pipeline.progress.total}
          />
          <div className="aq-pipe-dates">
            {pipeline.state === "done" && pipeline.completed_at ? (
              <>
                completed <span className="aq-mono">{formatTimestamp(pipeline.completed_at)}</span>
              </>
            ) : pipeline.started_at ? (
              <>
                started <span className="aq-mono">{formatTimestamp(pipeline.started_at)}</span>
              </>
            ) : (
              <>
                updated <span className="aq-mono">{formatTimestamp(pipeline.updated_at)}</span>
              </>
            )}
          </div>
        </div>
        <div className="aq-pipe-chips">
          <ToneChip label={pipeline.autonomy.label} tone={pipeline.autonomy.tone} />
          {attentionChips.length > 0 ? (
            attentionChips.map((chip) => (
              <ToneChip key={chip.label} label={chip.label} tone={chip.tone} />
            ))
          ) : (
            <ToneChip
              label={pipeline.state === "done" ? "all tasks done" : "no active flags"}
              tone={pipeline.state === "done" ? "ok" : "mute"}
            />
          )}
        </div>
      </button>

      {expanded && hasTasks ? (
        <div className="aq-dag-block">
          <div className="aq-dag-copy">
            <span className="aq-mono aq-mute">
              execution chain inline · read-only
            </span>
            <span className="aq-mono aq-mute">
              {pipeline.tasks.length} jobs · edges from task relations
            </span>
          </div>
          <PipelineDag
            onJobSelect={onJobSelect}
            pipeline={pipeline}
            selectedRef={selectedRef}
          />
        </div>
      ) : null}
    </>
  );
}

type PipelineDagProps = {
  onJobSelect: (ref: string, focusReturnId: string) => void;
  pipeline: PipelineSummary;
  selectedRef: string | null;
};

function PipelineDag({ onJobSelect, pipeline, selectedRef }: PipelineDagProps) {
  const layout = useMemo(() => layoutJobs(pipeline.tasks), [pipeline.tasks]);
  const selectedJob = selectedRef
    ? pipeline.tasks.find((job) => job.ref === selectedRef) ?? null
    : null;

  return (
    <div className="aq-dag-shell">
      <div className="aq-dag-scroll">
        <div
          className="aq-dag-stage"
          style={{ width: layout.width, height: layout.height }}
        >
          <svg className="aq-dag-svg" height={layout.height} width={layout.width}>
            {layout.edges.map((edge) => {
              const source = layout.positions.get(edge.from);
              const target = layout.positions.get(edge.to);
              if (!source || !target) {
                return null;
              }

              const cardWidth = 216;
              const cardHeight = 104;
              const startX = source.x + cardWidth;
              const startY = source.y + cardHeight / 2;
              const endX = target.x;
              const endY = target.y + cardHeight / 2;
              const midX = (startX + endX) / 2;

              return (
                <path
                  d={`M ${startX} ${startY} C ${midX} ${startY}, ${midX} ${endY}, ${endX} ${endY}`}
                  fill="none"
                  key={`${edge.kind}-${edge.from}-${edge.to}`}
                  opacity={edge.kind === "parent" ? 0.55 : 0.72}
                  stroke={
                    edge.kind === "parent"
                      ? "var(--border-strong)"
                      : "var(--tone-info-fg)"
                  }
                  strokeDasharray={edge.kind === "parent" ? "none" : "4 4"}
                  strokeWidth={edge.kind === "parent" ? 1.2 : 1.5}
                />
              );
            })}
          </svg>

          {pipeline.tasks.map((job) => {
            const position = layout.positions.get(job.ref);
            if (!position) {
              return null;
            }

            const buttonId = jobCardElementId(pipeline.id, job.ref);

            return (
              <div
                className="aq-dag-node"
                key={job.id}
                style={{ left: position.x, top: position.y }}
              >
                <JobCard
                  buttonId={buttonId}
                  job={job}
                  onClick={() => onJobSelect(job.ref, buttonId)}
                  selected={selectedJob?.ref === job.ref}
                />
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

type JobCardProps = {
  buttonId: string;
  job: PipelineJob;
  onClick: () => void;
  selected: boolean;
};

function JobCard({ buttonId, job, onClick, selected }: JobCardProps) {
  const tone = jobStateTone(job.status);

  return (
    <button
      aria-pressed={selected}
      className={`aq-jobcard aq-jobcard-${tone} ${selected ? "is-selected" : ""}`}
      id={buttonId}
      onClick={onClick}
      type="button"
    >
      <div className="aq-jobcard-strip">{JOB_ICON[job.status]}</div>
      <div className="aq-jobcard-body">
        <div className="aq-jobcard-ref aq-mono">{job.ref}</div>
        <div className="aq-jobcard-title">{job.title}</div>
        <div className="aq-jobcard-foot">
          <span className="aq-jobcard-agent aq-mono">
            {formatActor(job.claimed_by_actor_id)}
          </span>
          <span className="aq-jobcard-dur aq-mono">{job.raw_state}</span>
        </div>
      </div>
    </button>
  );
}

function jobCardElementId(pipelineId: string, ref: string) {
  const stableRef = ref.replace(/[^a-zA-Z0-9_-]/g, "-");
  return `aq-jobcard-${pipelineId}-${stableRef}`;
}

function layoutJobs(jobs: PipelineJob[]): JobLayout {
  const columns = new Map<number, PipelineJob[]>();
  const positions = new Map<string, { x: number; y: number }>();
  const byRef = new Map(jobs.map((job) => [job.ref, job]));
  const levelCache = new Map<string, number>();
  const visiting = new Set<string>();

  function levelFor(ref: string): number {
    const cached = levelCache.get(ref);
    if (cached !== undefined) {
      return cached;
    }
    if (visiting.has(ref)) {
      return 0;
    }

    visiting.add(ref);
    const job = byRef.get(ref);
    if (!job || job.dependency_refs.length === 0) {
      levelCache.set(ref, 0);
      visiting.delete(ref);
      return 0;
    }

    const parentLevel =
      Math.max(
        ...job.dependency_refs.map((dependencyRef) => levelFor(dependencyRef)),
      ) + 1;
    levelCache.set(ref, parentLevel);
    visiting.delete(ref);
    return parentLevel;
  }

  const sortedJobs = [...jobs].sort(comparePipelineJobs);
  for (const job of sortedJobs) {
    const level = levelFor(job.ref);
    const bucket = columns.get(level);
    if (bucket) {
      bucket.push(job);
    } else {
      columns.set(level, [job]);
    }
  }

  const cardWidth = 216;
  const cardHeight = 104;
  const gapX = 48;
  const gapY = 18;
  const orderedColumns = [...columns.entries()].sort(([left], [right]) => left - right);

  for (const [columnIndex, columnJobs] of orderedColumns) {
    columnJobs.forEach((job, rowIndex) => {
      positions.set(job.ref, {
        x: columnIndex * (cardWidth + gapX),
        y: rowIndex * (cardHeight + gapY),
      });
    });
  }

  const edges = sortedJobs.flatMap((job) => [
    ...(job.parent_ref
      ? [
          {
            from: job.parent_ref,
            to: job.ref,
            kind: "parent" as const,
          },
        ]
      : []),
    ...job.depends_on.map((relation) => ({
      from: relation.ref,
      to: job.ref,
      kind: "dependency" as const,
    })),
    ...job.blocked_by.map((relation) => ({
      from: relation.ref,
      to: job.ref,
      kind: "dependency" as const,
    })),
  ]);

  const maxColumn = Math.max(...orderedColumns.map(([column]) => column), 0);
  const maxRows = Math.max(...orderedColumns.map(([, columnJobs]) => columnJobs.length), 1);

  return {
    columns,
    positions,
    edges,
    width: (maxColumn + 1) * cardWidth + maxColumn * gapX,
    height: maxRows * cardHeight + (maxRows - 1) * gapY + 8,
  };
}

async function fetchPipelines(
  state: PipelineSectionState,
  authToken: string,
  signal: AbortSignal,
) {
  const cacheKey = `${state}:${authToken}`;
  const cachedPayload = readCachedPipelines(cacheKey);
  if (cachedPayload) {
    return cachedPayload;
  }

  const response = await fetch(`/api/v1/pipelines?state=${state}`, {
    headers: {
      Authorization: `Bearer ${authToken}`,
    },
    signal,
  });

  const payload = (await response.json().catch(() => null)) as
    | PipelinesResponse
    | { error?: string }
    | null;

  if (!response.ok || payload === null || !("pipelines" in payload)) {
    throw new Error(
      payload && "error" in payload && typeof payload.error === "string"
        ? payload.error
        : "Pipelines request failed.",
    );
  }

  writeCachedPipelines(cacheKey, payload);
  return payload;
}

function readCachedPipelines(cacheKey: string) {
  const entry = pipelinesResponseCache.get(cacheKey);
  if (!entry) {
    return null;
  }

  if (entry.expiresAt <= Date.now()) {
    pipelinesResponseCache.delete(cacheKey);
    return null;
  }

  pipelinesResponseCache.delete(cacheKey);
  pipelinesResponseCache.set(cacheKey, entry);
  return entry.payload;
}

function writeCachedPipelines(cacheKey: string, payload: PipelinesResponse) {
  pipelinesResponseCache.delete(cacheKey);
  pipelinesResponseCache.set(cacheKey, {
    expiresAt: Date.now() + PIPELINES_CACHE_TTL_MS,
    payload,
  });

  while (pipelinesResponseCache.size > PIPELINES_CACHE_MAX_ENTRIES) {
    const oldestKey = pipelinesResponseCache.keys().next().value;
    if (!oldestKey) {
      return;
    }
    pipelinesResponseCache.delete(oldestKey);
  }
}

function buildAttentionChips(pipeline: PipelineSummary) {
  const chips: Array<{ label: string; tone: PipelineTone }> = [];
  if (pipeline.attention.failed > 0) {
    chips.push({ label: `${pipeline.attention.failed} failed`, tone: "danger" });
  }
  if (pipeline.attention.needs_review > 0) {
    chips.push({
      label: `${pipeline.attention.needs_review} needs review`,
      tone: "warn",
    });
  }
  if (pipeline.attention.running > 0) {
    chips.push({ label: `${pipeline.attention.running} running`, tone: "info" });
  }
  if (pipeline.attention.queued > 0) {
    chips.push({ label: `${pipeline.attention.queued} queued`, tone: "mute" });
  }
  if (pipeline.attention.blocked > 0) {
    chips.push({ label: `${pipeline.attention.blocked} blocked`, tone: "warn" });
  }
  return chips;
}

function ProgressMeter({
  done,
  total,
  tone,
}: {
  done: number;
  total: number;
  tone: PipelineTone;
}) {
  const percentage = total > 0 ? Math.round((done / total) * 100) : 0;
  return (
    <div className="aq-progress">
      <div className="aq-progress-track">
        <div
          className="aq-progress-fill"
          style={{
            width: `${percentage}%`,
            background: TONE_ACCENT[tone],
          }}
        />
      </div>
      <span className="aq-progress-label">
        {done}/{total}
      </span>
    </div>
  );
}

function ToneChip({
  label,
  tone,
}: {
  label: string;
  tone: PipelineTone;
}) {
  return <span className={`aq-tone aq-tone-${tone}`}>{label}</span>;
}

function formatTimestamp(value: string) {
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(new Date(value));
}

function formatActor(actorId: string | null) {
  return actorId ? `@${actorId.slice(0, 8)}` : "unclaimed";
}

function jobStateTone(state: PipelineJobState): PipelineTone {
  if (state === "done") {
    return "ok";
  }
  if (state === "failed") {
    return "danger";
  }
  if (state === "review" || state === "blocked") {
    return "warn";
  }
  if (state === "running") {
    return "info";
  }
  return "mute";
}

function comparePipelineJobs(left: PipelineJob, right: PipelineJob) {
  if (left.sequence !== null && right.sequence !== null && left.sequence !== right.sequence) {
    return left.sequence - right.sequence;
  }
  return left.created_at.localeCompare(right.created_at);
}
