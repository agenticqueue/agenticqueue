"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  Background,
  Controls,
  MiniMap,
  ReactFlow,
  type Edge,
  type Node,
  type NodeTypes,
  type NodeProps,
} from "@xyflow/react";

import "@xyflow/react/dist/style.css";

type GraphMode = "decision" | "execution" | "dependency";
type GraphTone = "ok" | "info" | "warn" | "danger" | "mute";
type GraphKind = "job" | "decision" | "artifact";

type WorkStatus = "running" | "failed" | "review" | "queued" | "blocked" | "done";

type WorkOutput = {
  id: string;
  kind: string;
  label: string;
  uri: string;
  created_at: string;
  run_ref: string | null;
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

type DecisionScope = "global" | "project" | "task";
type DecisionStatus = "active" | "superseded";

type DecisionItem = {
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
};

type DecisionListResponse = {
  generated_at: string;
  count: number;
  items: DecisionItem[];
};

type GraphViewProps = {
  authToken: string;
};

type GraphNodeData = {
  dimmed: boolean;
  kind: GraphKind;
  ref: string;
  selected: boolean;
  summary: string;
  testId: string;
  title: string;
  tone: GraphTone;
};

type GraphFlowNode = Node<GraphNodeData, "graphEntity">;

type GraphSeedEdge = {
  id: string;
  kind: "artifact" | "blocked" | "dependency" | "linked" | "parent" | "supersedes";
  label: string;
  source: string;
  target: string;
};

type GraphCardBase = {
  id: string;
  kind: GraphKind;
  ref: string;
  summary: string;
  title: string;
  tone: GraphTone;
};

type GraphJobCard = GraphCardBase & {
  item: WorkItem;
  kind: "job";
};

type GraphDecisionCard = GraphCardBase & {
  item: DecisionItem;
  kind: "decision";
};

type GraphArtifactCard = GraphCardBase & {
  artifact: WorkOutput;
  jobRef: string;
  jobTitle: string;
  kind: "artifact";
  pipeline: string;
};

type GraphCard = GraphArtifactCard | GraphDecisionCard | GraphJobCard;

type GraphBundle = {
  allDescendants: Map<string, string[]>;
  edgeCount: number;
  edges: Edge[];
  entities: Map<string, GraphCard>;
  hiddenCount: number;
  nodes: GraphFlowNode[];
};

const GRAPH_TABS: Array<{
  description: string;
  label: string;
  value: GraphMode;
}> = [
  {
    value: "decision",
    label: "Decision map",
    description: "supersedes chains + linked jobs",
  },
  {
    value: "execution",
    label: "Execution chain",
    description: "jobs + produced artifacts",
  },
  {
    value: "dependency",
    label: "Dependency map",
    description: "depends-on + blocked-by topology",
  },
];

const NODE_WIDTH = 228;
const NODE_HEIGHT = 114;
const COLUMN_GAP = 76;
const ROW_GAP = 24;

const NODE_KIND_LABEL: Record<GraphKind, string> = {
  job: "job",
  decision: "decision",
  artifact: "artifact",
};

export function GraphView({ authToken }: GraphViewProps) {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [workItems, setWorkItems] = useState<WorkItem[]>([]);
  const [decisions, setDecisions] = useState<DecisionItem[]>([]);
  const [generatedAt, setGeneratedAt] = useState<string | null>(null);
  const [mode, setMode] = useState<GraphMode>("decision");
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [collapsedRoots, setCollapsedRoots] = useState<string[]>([]);
  const [refreshNonce, setRefreshNonce] = useState(0);
  const loadStartedAtRef = useRef<number | null>(null);
  const firstLoadRef = useRef(true);
  const [firstNodeRenderMs, setFirstNodeRenderMs] = useState<number | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      loadStartedAtRef.current = performance.now();
      setFirstNodeRenderMs(null);
      setLoading(firstLoadRef.current);
      setError(null);

      try {
        const [workResponse, decisionResponse] = await Promise.all([
          fetch("/api/v1/work", {
            headers: {
              Authorization: `Bearer ${authToken}`,
            },
            cache: "no-store",
          }),
          fetch("/api/v1/decisions", {
            headers: {
              Authorization: `Bearer ${authToken}`,
            },
            cache: "no-store",
          }),
        ]);

        const workPayload = (await workResponse.json().catch(() => null)) as
          | WorkResponse
          | { error?: string }
          | null;
        const decisionPayload = (await decisionResponse.json().catch(() => null)) as
          | DecisionListResponse
          | { error?: string }
          | null;

        if (
          !workResponse.ok ||
          workPayload === null ||
          !("items" in workPayload)
        ) {
          throw new Error(
            workPayload &&
              "error" in workPayload &&
              typeof workPayload.error === "string"
              ? workPayload.error
              : "Graph work request failed.",
          );
        }

        if (
          !decisionResponse.ok ||
          decisionPayload === null ||
          !("items" in decisionPayload)
        ) {
          throw new Error(
            decisionPayload &&
              "error" in decisionPayload &&
              typeof decisionPayload.error === "string"
              ? decisionPayload.error
              : "Graph decisions request failed.",
          );
        }

        if (cancelled) {
          return;
        }

        setWorkItems(workPayload.items);
        setDecisions(decisionPayload.items);
        setGeneratedAt(
          [workPayload.generated_at, decisionPayload.generated_at]
            .sort((left, right) => right.localeCompare(left))[0] ?? null,
        );
        firstLoadRef.current = false;
      } catch (requestError: unknown) {
        if (cancelled) {
          return;
        }

        setError(
          requestError instanceof Error
            ? requestError.message
            : "Failed to load the graph view.",
        );
        firstLoadRef.current = false;
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    }

    void load();

    const intervalId = window.setInterval(() => {
      if (!document.hidden) {
        void load();
      }
    }, 30_000);

    const handleVisibility = () => {
      if (!document.hidden) {
        void load();
      }
    };

    document.addEventListener("visibilitychange", handleVisibility);

    return () => {
      cancelled = true;
      window.clearInterval(intervalId);
      document.removeEventListener("visibilitychange", handleVisibility);
    };
  }, [authToken, refreshNonce]);

  useEffect(() => {
    setCollapsedRoots([]);
  }, [mode]);

  const graph = useMemo(
    () => buildGraphBundle({ mode, workItems, decisions, collapsedRoots, selectedNodeId }),
    [collapsedRoots, decisions, mode, selectedNodeId, workItems],
  );

  useEffect(() => {
    if (graph.nodes.length === 0) {
      if (selectedNodeId !== null) {
        setSelectedNodeId(null);
      }
      return;
    }

    if (!selectedNodeId || !graph.entities.has(selectedNodeId)) {
      setSelectedNodeId(graph.nodes[0]?.id ?? null);
    }
  }, [graph.entities, graph.nodes, selectedNodeId]);

  useEffect(() => {
    if (graph.nodes.length === 0 || firstNodeRenderMs !== null) {
      return;
    }

    const startedAt = loadStartedAtRef.current;
    if (startedAt === null) {
      return;
    }

    const frameId = window.requestAnimationFrame(() => {
      setFirstNodeRenderMs(performance.now() - startedAt);
    });

    return () => window.cancelAnimationFrame(frameId);
  }, [firstNodeRenderMs, graph.nodes.length]);

  const selectedEntity = selectedNodeId
    ? graph.entities.get(selectedNodeId) ?? null
    : null;
  const selectedDescendantCount =
    selectedNodeId && graph.allDescendants.has(selectedNodeId)
      ? graph.allDescendants.get(selectedNodeId)?.length ?? 0
      : 0;
  const selectedCollapsed =
    selectedNodeId !== null && collapsedRoots.includes(selectedNodeId);
  const renderBudget =
    firstNodeRenderMs !== null ? firstNodeRenderMs.toFixed(1) : "";

  return (
    <div className="aq-work-view aq-graph-view">
      <div className="aq-content-head aq-content-head-pipelines">
        <div>
          <p className="aq-content-eyebrow">Phase 7 live view</p>
          <h1 className="aq-content-title">Graph</h1>
        </div>
        <p className="aq-content-summary">
          Three read-only graph lenses over the workgraph: decision lineage,
          execution chains, and dependency topology. Pan, zoom, inspect, and
          keep every mutation outside the web shell.
        </p>
      </div>

      <div className="aq-pipelines-readonly">
        <span className="aq-pipelines-readonly-kicker">read-only</span>
        <span>
          Graph nodes come from the same work + decisions read models that feed
          the rest of Phase 7. No synthetic graph fixtures are used at runtime.
        </span>
      </div>

      <div className="aq-work-toolbar">
        <div className="aq-work-toolbar-meta">
          <span className="aq-mono aq-mute">
            {graph.nodes.length} visible nodes · {graph.edgeCount} visible edges
          </span>
          {graph.hiddenCount > 0 ? (
            <span className="aq-mono aq-mute">
              {graph.hiddenCount} descendants collapsed
            </span>
          ) : null}
          {generatedAt ? (
            <span className="aq-mono aq-mute">
              last sync {formatTimestamp(generatedAt)}
            </span>
          ) : null}
          <span className="aq-mono aq-mute">
            first node render {renderBudget || "pending"} ms
          </span>
        </div>
        <button
          className="aq-secondary-button"
          onClick={() => setRefreshNonce((current) => current + 1)}
          type="button"
        >
          Refresh
        </button>
      </div>

      <div className="aq-tab-strip" role="tablist" aria-label="Graph modes">
        {GRAPH_TABS.map((tab) => (
          <button
            aria-selected={mode === tab.value}
            className={`aq-tab-button ${mode === tab.value ? "is-selected" : ""}`}
            data-testid={`graph-tab-${tab.value}`}
            key={tab.value}
            onClick={() => setMode(tab.value)}
            role="tab"
            type="button"
          >
            {tab.label}
          </button>
        ))}
      </div>

      <div className="aq-graph-tab-copy">
        <span className="aq-mono aq-mute">
          {
            GRAPH_TABS.find((tab) => tab.value === mode)?.description
          }
        </span>
      </div>

      {loading ? (
        <div className="aq-pipelines-state">
          <p className="aq-auth-kicker">Loading graph</p>
          <h2 className="aq-state-title">Mapping the read-only workgraph</h2>
          <p className="aq-state-copy">
            Pulling work and decision entities, then laying them out into the
            active graph mode.
          </p>
        </div>
      ) : error ? (
        <div className="aq-pipelines-state is-error" role="alert">
          <p className="aq-auth-kicker">Load failure</p>
          <h2 className="aq-state-title">Graph could not be loaded</h2>
          <p className="aq-state-copy">{error}</p>
        </div>
      ) : graph.nodes.length === 0 ? (
        <div className="aq-empty aq-empty-pipelines" data-testid="graph-empty">
          <span className="aq-mono aq-mute">
            {"// No graph data yet — start a pipeline to populate"}
          </span>
        </div>
      ) : (
        <div className="aq-work-shell aq-graph-shell">
          <section className="aq-graph-panel">
            <div className="aq-graph-meta">
              <div className="aq-graph-legend">
                <span className="aq-mono aq-mute">job</span>
                <span className="aq-mono aq-mute">decision</span>
                <span className="aq-mono aq-mute">artifact</span>
              </div>
              <span className="aq-mono aq-mute">
                drag the canvas to pan · use built-in controls to zoom
              </span>
            </div>

            <div
              className="aq-graph-stage"
              data-first-node-ms={renderBudget}
              data-testid="graph-canvas"
            >
              <ReactFlow
                defaultViewport={{
                  x: 24,
                  y: 24,
                  zoom: graph.nodes.length > 18 ? 0.72 : 0.96,
                }}
                edges={graph.edges}
                fitView={graph.nodes.length <= 18}
                minZoom={0.25}
                maxZoom={1.6}
                nodeTypes={GRAPH_NODE_TYPES}
                nodes={graph.nodes}
                nodesConnectable={false}
                nodesDraggable={false}
                onNodeClick={(_, node) => setSelectedNodeId(node.id)}
                panOnDrag
                proOptions={{ hideAttribution: true }}
                selectionOnDrag={false}
                zoomOnDoubleClick={false}
              >
                <Background color="rgba(148, 163, 184, 0.16)" gap={18} size={1} />
                <MiniMap
                  className="aq-graph-minimap"
                  nodeColor={(node) => minimapColor(node as Node<GraphNodeData>)}
                  pannable
                  zoomable
                />
                <Controls showInteractive={false} />
              </ReactFlow>
            </div>
          </section>

          {selectedEntity ? (
            <aside className="aq-detail" data-testid="graph-detail">
              <div className="aq-detail-head">
                <div>
                  <p className="aq-auth-kicker">Selected node</p>
                  <h2 className="aq-detail-title">{selectedEntity.title}</h2>
                </div>
                <div className="aq-detail-status-row">
                  <span className={`aq-tone aq-tone-${selectedEntity.tone}`}>
                    {NODE_KIND_LABEL[selectedEntity.kind]}
                  </span>
                  <span
                    className="aq-mono aq-detail-ref"
                    data-testid="graph-detail-ref"
                  >
                    {selectedEntity.ref}
                  </span>
                </div>
              </div>

              <div className="aq-detail-section">
                <div className="aq-detail-section-label">Summary</div>
                <p className="aq-detail-prose">{selectedEntity.summary}</p>
              </div>

              {selectedEntity.kind === "job" ? (
                <JobDetailContent item={selectedEntity.item} />
              ) : null}

              {selectedEntity.kind === "decision" ? (
                <DecisionDetailContent item={selectedEntity.item} />
              ) : null}

              {selectedEntity.kind === "artifact" ? (
                <ArtifactDetailContent item={selectedEntity} />
              ) : null}

              {selectedDescendantCount > 0 ? (
                <div className="aq-detail-section">
                  <div className="aq-detail-section-label">Subtree</div>
                  <button
                    className="aq-secondary-button"
                    data-testid="graph-collapse-toggle"
                    onClick={() =>
                      setCollapsedRoots((current) =>
                        current.includes(selectedEntity.id)
                          ? current.filter((entry) => entry !== selectedEntity.id)
                          : [...current, selectedEntity.id],
                      )
                    }
                    type="button"
                  >
                    {selectedCollapsed
                      ? `Expand ${selectedDescendantCount} descendants`
                      : `Collapse ${selectedDescendantCount} descendants`}
                  </button>
                </div>
              ) : null}

              <div className="aq-job-detail-callout">
                <span className="aq-mono aq-mute">
                  Writes are disabled here. Use `aq`, REST, or MCP for task,
                  decision, and artifact actions.
                </span>
              </div>
            </aside>
          ) : (
            <aside className="aq-detail aq-detail-empty">
              <p className="aq-auth-kicker">No node selected</p>
              <h2 className="aq-detail-title">Choose a node to inspect it</h2>
              <p className="aq-detail-prose">
                Click any graph node to open the detail panel and highlight its
                immediate neighborhood.
              </p>
            </aside>
          )}
        </div>
      )}
    </div>
  );
}

function GraphEntityNode({ data }: NodeProps<GraphFlowNode>) {
  return (
    <div
      className={`aq-graph-node aq-graph-node-${data.kind} aq-graph-node-${data.tone} ${
        data.selected ? "is-selected" : ""
      } ${data.dimmed ? "is-dimmed" : ""}`}
      data-kind={data.kind}
      data-testid={data.testId}
      data-tone={data.tone}
    >
      <div className="aq-graph-node-head">
        <span className="aq-graph-node-kind">{NODE_KIND_LABEL[data.kind]}</span>
        <span className="aq-graph-node-ref aq-mono">{data.ref}</span>
      </div>
      <div className="aq-graph-node-title">{data.title}</div>
      <div className="aq-graph-node-summary">{data.summary}</div>
    </div>
  );
}

function JobDetailContent({ item }: { item: WorkItem }) {
  return (
    <>
      <div className="aq-detail-props">
        <PropertyCard label="Pipeline" value={item.pipeline} />
        <PropertyCard label="Task type" value={item.task_type} />
        <PropertyCard label="Actor" value={item.actor ? `@${item.actor}` : "unclaimed"} />
        <PropertyCard label="Status" value={item.status} />
        <PropertyCard label="Updated" value={formatTimestamp(item.updated_at)} />
        <PropertyCard label="Priority" value={String(item.priority)} />
      </div>

      <RelationSection
        hrefPrefix="/work?job="
        label="Parent"
        refs={asRefs(item.parent_ref)}
      />
      <RelationSection
        hrefPrefix="/work?job="
        label="Depends on"
        refs={item.dependency_refs}
      />
      <RelationSection
        hrefPrefix="/work?job="
        label="Blocked by"
        refs={item.blocked_by_refs}
      />
      <RelationSection
        hrefPrefix="/work?job="
        label="Blocks"
        refs={item.block_refs}
      />
      <RelationSection
        hrefPrefix="/work?job="
        label="Children"
        refs={item.child_refs}
      />

      {item.outputs.length > 0 ? (
        <div className="aq-detail-section">
          <div className="aq-detail-section-label">Outputs</div>
          <div className="aq-linked-jobs">
            {item.outputs.map((output) => (
              <span className="aq-prop-link aq-mono" key={output.id}>
                {output.label}
              </span>
            ))}
          </div>
        </div>
      ) : null}
    </>
  );
}

function DecisionDetailContent({ item }: { item: DecisionItem }) {
  const linkedJobs = uniqueRefs(
    [item.primary_job_ref, ...item.linked_job_refs].filter(isDefinedString),
  );

  return (
    <>
      <div className="aq-detail-props">
        <PropertyCard label="Scope" value={item.scope} />
        <PropertyCard label="Status" value={item.status} />
        <PropertyCard label="Actor" value={item.actor ? `@${item.actor}` : "system"} />
        <PropertyCard label="Project" value={item.project_name ?? item.project_slug ?? "n/a"} />
        <PropertyCard label="Decided" value={formatTimestamp(item.decided_at)} />
        <PropertyCard
          label="Primary job"
          value={item.primary_job_ref ?? "none linked"}
        />
      </div>

      {item.rationale ? (
        <div className="aq-detail-section">
          <div className="aq-detail-section-label">Rationale</div>
          <p className="aq-detail-prose">{item.rationale}</p>
        </div>
      ) : null}

      <RelationSection
        hrefPrefix="/decisions"
        label="Supersedes"
        refs={item.supersedes_refs}
      />
      <RelationSection
        hrefPrefix="/decisions"
        label="Superseded by"
        refs={item.superseded_by_refs}
      />
      <RelationSection hrefPrefix="/work?job=" label="Linked jobs" refs={linkedJobs} />
    </>
  );
}

function ArtifactDetailContent({ item }: { item: GraphArtifactCard }) {
  return (
    <>
      <div className="aq-detail-props">
        <PropertyCard label="Pipeline" value={item.pipeline} />
        <PropertyCard label="Output kind" value={item.artifact.kind} />
        <PropertyCard label="Produced by" value={item.jobRef} />
        <PropertyCard label="Created" value={formatTimestamp(item.artifact.created_at)} />
        <PropertyCard label="Run ref" value={item.artifact.run_ref ?? "n/a"} />
        <PropertyCard label="URI" value={item.artifact.uri} />
      </div>

      <div className="aq-detail-section">
        <div className="aq-detail-section-label">Produced by</div>
        <Link
          className="aq-prop-link aq-linked-job aq-mono"
          href={`/work?job=${encodeURIComponent(item.jobRef)}`}
        >
          {item.jobRef} · {item.jobTitle}
        </Link>
      </div>
    </>
  );
}

function RelationSection({
  hrefPrefix,
  label,
  refs,
}: {
  hrefPrefix: string;
  label: string;
  refs: string[];
}) {
  if (refs.length === 0) {
    return null;
  }

  return (
    <div className="aq-detail-section">
      <div className="aq-detail-section-label">{label}</div>
      <div className="aq-linked-jobs">
        {refs.map((ref) => (
          <Link
            className="aq-prop-link aq-linked-job aq-mono"
            href={`${hrefPrefix}${hrefPrefix.includes("?") ? "" : "#"}${encodeURIComponent(ref)}`}
            key={`${label}-${ref}`}
          >
            {ref}
          </Link>
        ))}
      </div>
    </div>
  );
}

function PropertyCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="aq-prop">
      <span className="aq-prop-k">{label}</span>
      <span className="aq-prop-v aq-mono">{value}</span>
    </div>
  );
}

function buildGraphBundle({
  mode,
  workItems,
  decisions,
  collapsedRoots,
  selectedNodeId,
}: {
  mode: GraphMode;
  workItems: WorkItem[];
  decisions: DecisionItem[];
  collapsedRoots: string[];
  selectedNodeId: string | null;
}): GraphBundle {
  const workByRef = new Map(workItems.map((item) => [item.ref, item]));
  const decisionByRef = new Map(decisions.map((item) => [item.ref, item]));
  const cards = new Map<string, GraphCard>();
  const edges: GraphSeedEdge[] = [];

  function ensureJobCard(item: WorkItem) {
    const nodeId = `job:${item.id}`;
    if (!cards.has(nodeId)) {
      cards.set(nodeId, {
        id: nodeId,
        kind: "job",
        ref: item.ref,
        title: item.title,
        summary: `${item.pipeline} · ${item.status}`,
        tone: workTone(item.status),
        item,
      });
    }
    return nodeId;
  }

  function ensureDecisionCard(item: DecisionItem) {
    const nodeId = `decision:${item.id}`;
    if (!cards.has(nodeId)) {
      cards.set(nodeId, {
        id: nodeId,
        kind: "decision",
        ref: item.ref,
        title: item.title,
        summary: `${item.scope} · ${item.status}`,
        tone: item.status === "active" ? "ok" : "warn",
        item,
      });
    }
    return nodeId;
  }

  function ensureArtifactCard(output: WorkOutput, owner: WorkItem) {
    const nodeId = `artifact:${output.id}`;
    if (!cards.has(nodeId)) {
      cards.set(nodeId, {
        id: nodeId,
        kind: "artifact",
        ref: output.label,
        title: output.kind,
        summary: truncate(output.uri, 72),
        tone: "info",
        artifact: output,
        jobRef: owner.ref,
        jobTitle: owner.title,
        pipeline: owner.pipeline,
      });
    }
    return nodeId;
  }

  function pushEdge(edge: GraphSeedEdge) {
    if (
      !edges.some(
        (candidate) =>
          candidate.source === edge.source &&
          candidate.target === edge.target &&
          candidate.kind === edge.kind,
      )
    ) {
      edges.push(edge);
    }
  }

  if (mode === "decision") {
    for (const decision of decisions) {
      const decisionId = ensureDecisionCard(decision);
      for (const supersededRef of decision.supersedes_refs) {
        const olderDecision = decisionByRef.get(supersededRef);
        if (!olderDecision) {
          continue;
        }
        pushEdge({
          id: `supersedes:${olderDecision.id}:${decision.id}`,
          kind: "supersedes",
          label: "supersedes",
          source: ensureDecisionCard(olderDecision),
          target: decisionId,
        });
      }

      for (const jobRef of uniqueRefs(
        [decision.primary_job_ref, ...decision.linked_job_refs].filter(isDefinedString),
      )) {
        const job = workByRef.get(jobRef);
        if (!job) {
          continue;
        }
        pushEdge({
          id: `linked:${decision.id}:${job.id}`,
          kind: "linked",
          label: "linked job",
          source: decisionId,
          target: ensureJobCard(job),
        });
      }
    }
  }

  if (mode === "execution") {
    for (const job of workItems) {
      const jobId = ensureJobCard(job);

      if (job.parent_ref) {
        const parent = workByRef.get(job.parent_ref);
        if (parent) {
          pushEdge({
            id: `parent:${parent.id}:${job.id}`,
            kind: "parent",
            label: "parent",
            source: ensureJobCard(parent),
            target: jobId,
          });
        }
      }

      for (const output of job.outputs) {
        pushEdge({
          id: `artifact:${job.id}:${output.id}`,
          kind: "artifact",
          label: output.kind,
          source: jobId,
          target: ensureArtifactCard(output, job),
        });
      }
    }
  }

  if (mode === "dependency") {
    for (const job of workItems) {
      const jobId = ensureJobCard(job);

      for (const dependencyRef of uniqueRefs(job.dependency_refs)) {
        const dependency = workByRef.get(dependencyRef);
        if (!dependency) {
          continue;
        }
        pushEdge({
          id: `dependency:${dependency.id}:${job.id}`,
          kind: "dependency",
          label: "depends on",
          source: ensureJobCard(dependency),
          target: jobId,
        });
      }

      for (const blockedByRef of uniqueRefs(job.blocked_by_refs)) {
        const blocker = workByRef.get(blockedByRef);
        if (!blocker) {
          continue;
        }
        pushEdge({
          id: `blocked:${blocker.id}:${job.id}`,
          kind: "blocked",
          label: "blocked by",
          source: ensureJobCard(blocker),
          target: jobId,
        });
      }
    }
  }

  const outgoing = new Map<string, string[]>();
  const incoming = new Map<string, string[]>();

  for (const edge of edges) {
    if (!outgoing.has(edge.source)) {
      outgoing.set(edge.source, []);
    }
    outgoing.get(edge.source)?.push(edge.target);

    if (!incoming.has(edge.target)) {
      incoming.set(edge.target, []);
    }
    incoming.get(edge.target)?.push(edge.source);
  }

  const hiddenIds = new Set<string>();
  for (const rootId of collapsedRoots) {
    for (const descendantId of collectDescendants(rootId, outgoing)) {
      hiddenIds.add(descendantId);
    }
  }

  const visibleCards = [...cards.values()].filter((card) => !hiddenIds.has(card.id));
  const visibleEdges = edges.filter(
    (edge) => !hiddenIds.has(edge.source) && !hiddenIds.has(edge.target),
  );
  const visibleOutgoing = buildAdjacency(visibleEdges, "source");
  const visibleIncoming = buildAdjacency(visibleEdges, "target");
  const allDescendants = new Map<string, string[]>();
  for (const card of cards.values()) {
    allDescendants.set(card.id, collectDescendants(card.id, outgoing));
  }

  const selectedNeighbors = new Set<string>();
  if (selectedNodeId) {
    for (const neighbor of visibleOutgoing.get(selectedNodeId) ?? []) {
      selectedNeighbors.add(neighbor);
    }
    for (const neighbor of visibleIncoming.get(selectedNodeId) ?? []) {
      selectedNeighbors.add(neighbor);
    }
  }

  const { positions, width, height } = layoutCards(visibleCards, visibleEdges);

  const nodes: GraphFlowNode[] = visibleCards.map((card) => ({
    id: card.id,
    data: {
      dimmed:
        selectedNodeId !== null &&
        card.id !== selectedNodeId &&
        !selectedNeighbors.has(card.id),
      kind: card.kind,
      ref: card.ref,
      selected: card.id === selectedNodeId,
      summary: card.summary,
      testId: nodeTestId(card.ref),
      title: card.title,
      tone: card.tone,
    },
    position: positions.get(card.id) ?? { x: 0, y: 0 },
    style: {
      width: NODE_WIDTH,
      height: NODE_HEIGHT,
    },
    type: "graphEntity",
  }));

  const reactFlowEdges: Edge[] = visibleEdges.map((edge) => ({
    id: edge.id,
    source: edge.source,
    target: edge.target,
    type: "smoothstep",
    animated: edge.kind === "dependency" || edge.kind === "blocked",
    label: edge.label,
    style: {
      opacity:
        selectedNodeId !== null &&
        edge.source !== selectedNodeId &&
        edge.target !== selectedNodeId
          ? 0.22
          : 0.9,
      stroke: edgeColor(edge.kind),
      strokeWidth: edge.kind === "parent" ? 1.25 : 1.65,
    },
  }));

  return {
    allDescendants,
    edgeCount: reactFlowEdges.length,
    edges: reactFlowEdges,
    entities: new Map(visibleCards.map((card) => [card.id, card])),
    hiddenCount: hiddenIds.size,
    nodes: nodes.map((node) => ({
      ...node,
      position: normalizePosition(node.position, width, height),
    })),
  };
}

function layoutCards(cards: GraphCard[], edges: GraphSeedEdge[]) {
  const incoming = buildAdjacency(edges, "target");
  const levelCache = new Map<string, number>();
  const visiting = new Set<string>();

  function levelFor(nodeId: string): number {
    const cached = levelCache.get(nodeId);
    if (cached !== undefined) {
      return cached;
    }
    if (visiting.has(nodeId)) {
      return 0;
    }

    visiting.add(nodeId);
    const predecessors = incoming.get(nodeId) ?? [];
    const level =
      predecessors.length === 0
        ? 0
        : Math.max(...predecessors.map((predecessor) => levelFor(predecessor))) + 1;
    visiting.delete(nodeId);
    levelCache.set(nodeId, level);
    return level;
  }

  const columns = new Map<number, GraphCard[]>();
  for (const card of cards) {
    const level = levelFor(card.id);
    if (!columns.has(level)) {
      columns.set(level, []);
    }
    columns.get(level)?.push(card);
  }

  const positions = new Map<string, { x: number; y: number }>();
  const orderedColumns = [...columns.entries()].sort(([left], [right]) => left - right);

  orderedColumns.forEach(([level, columnCards]) => {
    const sortedCards = [...columnCards].sort(compareCards);
    sortedCards.forEach((card, index) => {
      positions.set(card.id, {
        x: level * (NODE_WIDTH + COLUMN_GAP),
        y: index * (NODE_HEIGHT + ROW_GAP),
      });
    });
  });

  const maxColumn = Math.max(...orderedColumns.map(([level]) => level), 0);
  const maxRows = Math.max(...orderedColumns.map(([, column]) => column.length), 1);

  return {
    positions,
    width: (maxColumn + 1) * NODE_WIDTH + maxColumn * COLUMN_GAP + 80,
    height: maxRows * NODE_HEIGHT + (maxRows - 1) * ROW_GAP + 80,
  };
}

function buildAdjacency(edges: GraphSeedEdge[], key: "source" | "target") {
  const adjacency = new Map<string, string[]>();

  for (const edge of edges) {
    const from = key === "source" ? edge.source : edge.target;
    const to = key === "source" ? edge.target : edge.source;
    if (!adjacency.has(from)) {
      adjacency.set(from, []);
    }
    adjacency.get(from)?.push(to);
  }

  return adjacency;
}

function collectDescendants(rootId: string, adjacency: Map<string, string[]>) {
  const descendants = new Set<string>();
  const queue = [...(adjacency.get(rootId) ?? [])];

  while (queue.length > 0) {
    const nextId = queue.shift();
    if (!nextId || descendants.has(nextId)) {
      continue;
    }

    descendants.add(nextId);
    for (const childId of adjacency.get(nextId) ?? []) {
      if (!descendants.has(childId)) {
        queue.push(childId);
      }
    }
  }

  return [...descendants];
}

function normalizePosition(
  position: { x: number; y: number },
  width: number,
  height: number,
) {
  return {
    x: Math.min(position.x + 40, Math.max(width - NODE_WIDTH, 0)),
    y: Math.min(position.y + 40, Math.max(height - NODE_HEIGHT, 0)),
  };
}

function nodeTestId(ref: string) {
  return `graph-node-${ref.toLowerCase().replace(/[^a-z0-9]+/g, "-")}`;
}

function edgeColor(kind: GraphSeedEdge["kind"]) {
  if (kind === "blocked") {
    return "var(--tone-warn-fg)";
  }
  if (kind === "artifact") {
    return "var(--tone-info-fg)";
  }
  if (kind === "supersedes") {
    return "var(--tone-ok-fg)";
  }
  return "var(--accent)";
}

function workTone(status: WorkStatus): GraphTone {
  if (status === "done") {
    return "ok";
  }
  if (status === "failed") {
    return "danger";
  }
  if (status === "review" || status === "blocked") {
    return "warn";
  }
  if (status === "running") {
    return "info";
  }
  return "mute";
}

function minimapColor(node: Node<GraphNodeData>) {
  if (node.data.kind === "decision") {
    return node.data.tone === "ok" ? "#67e8a5" : "#ffd76f";
  }
  if (node.data.kind === "artifact") {
    return "#8fb2ff";
  }
  if (node.data.tone === "danger") {
    return "#ff9e95";
  }
  if (node.data.tone === "warn") {
    return "#ffd76f";
  }
  if (node.data.tone === "ok") {
    return "#67e8a5";
  }
  return "#8fb2ff";
}

function compareCards(left: GraphCard, right: GraphCard) {
  const kindRank: Record<GraphKind, number> = {
    decision: 0,
    job: 1,
    artifact: 2,
  };

  const kindDelta = kindRank[left.kind] - kindRank[right.kind];
  if (kindDelta !== 0) {
    return kindDelta;
  }

  const leftRefNumber = parseRefNumber(left.ref);
  const rightRefNumber = parseRefNumber(right.ref);

  if (leftRefNumber !== null && rightRefNumber !== null && leftRefNumber !== rightRefNumber) {
    return leftRefNumber - rightRefNumber;
  }

  return left.ref.localeCompare(right.ref);
}

function parseRefNumber(value: string) {
  const match = value.match(/(\d+)/);
  return match ? Number.parseInt(match[1] ?? "", 10) : null;
}

function formatTimestamp(value: string) {
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(value));
}

function truncate(value: string, maxLength: number) {
  if (value.length <= maxLength) {
    return value;
  }
  return `${value.slice(0, maxLength - 1)}…`;
}

function uniqueRefs(refs: string[]) {
  return [...new Set(refs)].sort(compareRefStrings);
}

function compareRefStrings(left: string, right: string) {
  const leftNumber = parseRefNumber(left);
  const rightNumber = parseRefNumber(right);

  if (leftNumber !== null && rightNumber !== null && leftNumber !== rightNumber) {
    return leftNumber - rightNumber;
  }

  return left.localeCompare(right);
}

function asRefs(value: string | null) {
  return value ? [value] : [];
}

function isDefinedString(value: string | null): value is string {
  return Boolean(value);
}

const GRAPH_NODE_TYPES = {
  graphEntity: GraphEntityNode,
} satisfies NodeTypes;
