"use client";

import Link from "next/link";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import {
  JobDetailPanel,
  type JobDetailPanelJob,
} from "@/components/job-detail-panel";

type DecisionScope = "global" | "project" | "task";
type DecisionStatus = "active" | "superseded";
type DateRangeFilter = "all" | "24h" | "7d" | "30d";
type FilterValue = "all" | string;

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
  alternative_refs: Array<{
    id: string;
    ref: string;
    title: string;
  }>;
};

type DecisionListResponse = {
  generated_at: string;
  count: number;
  items: DecisionItem[];
};

type DecisionLineageNode = {
  id: string;
  ref: string;
  title: string;
  decided_at: string;
  status: DecisionStatus;
  scope: DecisionScope;
  relation: "selected" | "newer" | "older";
  depth: number;
};

type DecisionLineageResponse = {
  generated_at: string;
  decision_id: string;
  nodes: DecisionLineageNode[];
  edges: Array<{
    from_id: string;
    to_id: string;
    from_ref: string;
    to_ref: string;
  }>;
};

type DecisionsViewProps = {
  authToken: string;
};

const DATE_RANGE_OPTIONS: Array<{ value: DateRangeFilter; label: string }> = [
  { value: "all", label: "All time" },
  { value: "24h", label: "Last 24h" },
  { value: "7d", label: "Last 7d" },
  { value: "30d", label: "Last 30d" },
];

const STATUS_OPTIONS: Array<{ value: FilterValue; label: string }> = [
  { value: "all", label: "All statuses" },
  { value: "active", label: "Active" },
  { value: "superseded", label: "Superseded" },
];

const SCOPE_OPTIONS: Array<{ value: FilterValue; label: string }> = [
  { value: "all", label: "All scopes" },
  { value: "global", label: "Global" },
  { value: "project", label: "Project" },
  { value: "task", label: "Task" },
];

const DECISIONS_REFRESH_INTERVAL_MS = 30_000;
const DECISION_PANEL_CALLOUT =
  "Writes stay outside the UI. Use `aq`, REST, or MCP to create, supersede, or retract decisions.";

export function DecisionsView({ authToken }: DecisionsViewProps) {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [items, setItems] = useState<DecisionItem[]>([]);
  const [generatedAt, setGeneratedAt] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [lineage, setLineage] = useState<DecisionLineageResponse | null>(null);
  const [lineageLoading, setLineageLoading] = useState(false);
  const [lineageError, setLineageError] = useState<string | null>(null);
  const [scopeFilter, setScopeFilter] = useState<FilterValue>("all");
  const [statusFilter, setStatusFilter] = useState<FilterValue>("all");
  const [dateRangeFilter, setDateRangeFilter] =
    useState<DateRangeFilter>("all");
  const [refreshNonce, setRefreshNonce] = useState(0);
  const focusReturnIdRef = useRef<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        setLoading((current) => current && items.length === 0);
        setError(null);

        const response = await fetch("/api/v1/decisions", {
          headers: {
            Authorization: `Bearer ${authToken}`,
          },
          cache: "no-store",
        });

        const payload = (await response.json().catch(() => null)) as
          | DecisionListResponse
          | { error?: string }
          | null;

        if (!response.ok || payload === null || !("items" in payload)) {
          throw new Error(
            payload && "error" in payload && typeof payload.error === "string"
              ? payload.error
              : "Decisions request failed.",
          );
        }

        if (cancelled) {
          return;
        }

        setItems(payload.items);
        setGeneratedAt(payload.generated_at);
      } catch (requestError: unknown) {
        if (cancelled) {
          return;
        }

        setError(
          requestError instanceof Error
            ? requestError.message
            : "Failed to load decisions view.",
        );
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
    }, DECISIONS_REFRESH_INTERVAL_MS);

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
  }, [authToken, items.length, refreshNonce]);

  const filteredItems = useMemo(
    () =>
      items.filter((item) =>
        matchesFilters(item, {
          scopeFilter,
          statusFilter,
          dateRangeFilter,
        }),
      ),
    [dateRangeFilter, items, scopeFilter, statusFilter],
  );

  const selectedItem = useMemo(
    () => filteredItems.find((item) => item.id === selectedId) ?? null,
    [filteredItems, selectedId],
  );

  const selectedPanelJob = useMemo(
    () => (selectedItem ? toDecisionPanelJob(selectedItem) : null),
    [selectedItem],
  );

  const closeSelectedItem = useCallback(() => {
    const focusReturnId = focusReturnIdRef.current;
    setSelectedId(null);

    if (focusReturnId) {
      window.requestAnimationFrame(() => {
        document.getElementById(focusReturnId)?.focus();
      });
    }
  }, []);

  const selectItem = useCallback((itemId: string, focusReturnId?: string) => {
    if (focusReturnId) {
      focusReturnIdRef.current = focusReturnId;
    }
    setSelectedId(itemId);
  }, []);

  const selectLineageItem = useCallback(
    (itemId: string) => {
      const nextItem = items.find((item) => item.id === itemId);
      if (!nextItem) {
        return;
      }

      if (scopeFilter !== "all" && nextItem.scope !== scopeFilter) {
        setScopeFilter("all");
      }
      if (statusFilter !== "all" && nextItem.status !== statusFilter) {
        setStatusFilter("all");
      }
      setSelectedId(nextItem.id);
    },
    [items, scopeFilter, statusFilter],
  );

  const scopeCounts = useMemo(
    () =>
      buildFilterCounts({
        items,
        filterKey: "scope",
        options: SCOPE_OPTIONS.map((option) => option.value),
        scopeFilter,
        statusFilter,
        dateRangeFilter,
      }),
    [dateRangeFilter, items, scopeFilter, statusFilter],
  );

  const statusCounts = useMemo(
    () =>
      buildFilterCounts({
        items,
        filterKey: "status",
        options: STATUS_OPTIONS.map((option) => option.value),
        scopeFilter,
        statusFilter,
        dateRangeFilter,
      }),
    [dateRangeFilter, items, scopeFilter, statusFilter],
  );

  const dateRangeCounts = useMemo(
    () =>
      buildDateRangeCounts({
        items,
        scopeFilter,
        statusFilter,
      }),
    [items, scopeFilter, statusFilter],
  );

  useEffect(() => {
    if (!selectedId) {
      setLineage(null);
      setLineageError(null);
      return;
    }

    let cancelled = false;

    async function loadLineage() {
      try {
        setLineageLoading(true);
        setLineageError(null);

        const response = await fetch(`/api/v1/decisions/${selectedId}/lineage`, {
          headers: {
            Authorization: `Bearer ${authToken}`,
          },
          cache: "no-store",
        });

        const payload = (await response.json().catch(() => null)) as
          | DecisionLineageResponse
          | { error?: string }
          | null;

        if (!response.ok || payload === null || !("nodes" in payload)) {
          throw new Error(
            payload && "error" in payload && typeof payload.error === "string"
              ? payload.error
              : "Decision lineage request failed.",
          );
        }

        if (cancelled) {
          return;
        }

        setLineage(payload);
      } catch (requestError: unknown) {
        if (cancelled) {
          return;
        }

        setLineageError(
          requestError instanceof Error
            ? requestError.message
            : "Failed to load decision lineage.",
        );
      } finally {
        if (!cancelled) {
          setLineageLoading(false);
        }
      }
    }

    void loadLineage();

    return () => {
      cancelled = true;
    };
  }, [authToken, selectedId]);

  useEffect(() => {
    if (!selectedId) {
      return;
    }

    if (!filteredItems.some((item) => item.id === selectedId)) {
      setSelectedId(null);
    }
  }, [filteredItems, selectedId]);

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      const target = event.target;
      if (
        target instanceof HTMLInputElement ||
        target instanceof HTMLTextAreaElement ||
        target instanceof HTMLSelectElement
      ) {
        return;
      }

      if (event.key === "Escape") {
        if (selectedId) {
          event.preventDefault();
          closeSelectedItem();
        }
        return;
      }

      if (
        event.key !== "ArrowDown" &&
        event.key !== "ArrowUp" &&
        event.key !== "ArrowLeft" &&
        event.key !== "ArrowRight"
      ) {
        return;
      }

      if (filteredItems.length === 0) {
        return;
      }

      event.preventDefault();
      const currentIndex = selectedId
        ? filteredItems.findIndex((item) => item.id === selectedId)
        : -1;
      const direction =
        event.key === "ArrowUp" || event.key === "ArrowLeft" ? -1 : 1;
      const nextIndex =
        currentIndex === -1
          ? direction < 0
            ? filteredItems.length - 1
            : 0
          : (currentIndex + direction + filteredItems.length) %
            filteredItems.length;
      setSelectedId(filteredItems[nextIndex]?.id ?? null);
    }

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [closeSelectedItem, filteredItems, selectedId]);

  const newerLineage = useMemo(
    () =>
      lineage?.nodes
        .filter((node) => node.relation === "newer")
        .sort((left, right) => left.depth - right.depth) ?? [],
    [lineage],
  );
  const selectedLineageNode = useMemo(
    () => lineage?.nodes.find((node) => node.relation === "selected") ?? null,
    [lineage],
  );
  const olderLineage = useMemo(
    () =>
      lineage?.nodes
        .filter((node) => node.relation === "older")
        .sort((left, right) => left.depth - right.depth) ?? [],
    [lineage],
  );

  return (
    <div className="aq-work-view">
      <div className="aq-content-head aq-content-head-pipelines">
        <div>
          <p className="aq-content-eyebrow">Phase 7 live view</p>
          <h1 className="aq-content-title">Decisions</h1>
        </div>
        <p className="aq-content-summary">
          Read-only governance ledger for decisions, supersede chains, and
          linked Jobs without leaving the always-on shell.
        </p>
      </div>

      <div className="aq-pipelines-readonly">
        <span className="aq-pipelines-readonly-kicker">read-only</span>
        <span>
          This view mirrors the public decision ledger. Create, supersede, and
          retract actions stay in CLI, REST, and MCP surfaces.
        </span>
      </div>

      <div className="aq-work-toolbar">
        <div className="aq-work-toolbar-meta">
          <span className="aq-mono aq-mute">
            {filteredItems.length} visible · {items.length} total
          </span>
          {generatedAt ? (
            <span className="aq-mono aq-mute">
              last sync {formatTimestamp(generatedAt)}
            </span>
          ) : null}
        </div>
        <button
          className="aq-secondary-button"
          onClick={() => setRefreshNonce((current) => current + 1)}
          type="button"
        >
          Refresh
        </button>
      </div>

      <div className="aq-filter-stack">
        <FilterGroup
          counts={scopeCounts}
          label="Scope"
          onSelect={setScopeFilter}
          options={SCOPE_OPTIONS}
          selected={scopeFilter}
        />
        <FilterGroup
          counts={statusCounts}
          label="Status"
          onSelect={setStatusFilter}
          options={STATUS_OPTIONS}
          selected={statusFilter}
        />
        <FilterGroup
          counts={dateRangeCounts}
          label="Date range"
          onSelect={setDateRangeFilter}
          options={DATE_RANGE_OPTIONS}
          selected={dateRangeFilter}
        />
      </div>

      {loading ? (
        <div className="aq-pipelines-state">
          <p className="aq-auth-kicker">Loading decisions</p>
          <h2 className="aq-state-title">Assembling the governance ledger</h2>
          <p className="aq-state-copy">
            Pulling decision records, supersedes edges, and linked Job refs into
            one read-only surface.
          </p>
        </div>
      ) : error ? (
        <div className="aq-pipelines-state is-error" role="alert">
          <p className="aq-auth-kicker">Load failure</p>
          <h2 className="aq-state-title">Decisions could not be loaded</h2>
          <p className="aq-state-copy">{error}</p>
        </div>
      ) : filteredItems.length === 0 ? (
        <div className="aq-empty aq-empty-pipelines">
          <span className="aq-mono aq-mute">
            {"// no decisions match the current scope + status + date filters"}
          </span>
        </div>
      ) : (
        <section className="aq-knowledge-list" aria-label="Decisions list">
          {filteredItems.map((item) => {
            const rowId = decisionRowElementId(item.ref);
            return (
              <button
                aria-pressed={selectedItem?.id === item.id}
                className={`aq-knowledge-row ${selectedItem?.id === item.id ? "is-selected" : ""}`}
                data-testid={`decision-row-${item.ref}`}
                id={rowId}
                key={item.id}
                onClick={() => selectItem(item.id, rowId)}
                type="button"
              >
                <span className="aq-knowledge-ref aq-mono aq-mute">
                  {item.ref}
                </span>
                <span className="aq-knowledge-title">{item.title}</span>
                <span className="aq-knowledge-meta aq-mono aq-mute">
                  <span className="aq-knowledge-pipe">
                    {item.project_name ?? item.scope}
                  </span>
                  {item.linked_job_refs.length > 0 ? (
                    <>
                      <span className="aq-knowledge-sep">·</span>
                      <span>{item.linked_job_refs.join(", ")}</span>
                    </>
                  ) : null}
                </span>
              </button>
            );
          })}
        </section>
      )}

      {selectedItem && selectedPanelJob ? (
        <JobDetailPanel
          callout={DECISION_PANEL_CALLOUT}
          eyebrow="Selected decision"
          job={selectedPanelJob}
          onClose={closeSelectedItem}
          onSelect={() => undefined}
          open
          pipelineName={selectedItem.project_name ?? "Decisions"}
          testId="decision-detail"
        >
          <DecisionPanelSections
            item={selectedItem}
            lineage={lineage}
            lineageError={lineageError}
            lineageLoading={lineageLoading}
            newerLineage={newerLineage}
            olderLineage={olderLineage}
            onSelectLineage={selectLineageItem}
            selectedLineageNode={selectedLineageNode}
          />
        </JobDetailPanel>
      ) : null}
    </div>
  );
}

function DecisionPanelSections({
  item,
  lineage,
  lineageError,
  lineageLoading,
  newerLineage,
  olderLineage,
  onSelectLineage,
  selectedLineageNode,
}: {
  item: DecisionItem;
  lineage: DecisionLineageResponse | null;
  lineageError: string | null;
  lineageLoading: boolean;
  newerLineage: DecisionLineageNode[];
  olderLineage: DecisionLineageNode[];
  onSelectLineage: (id: string) => void;
  selectedLineageNode: DecisionLineageNode | null;
}) {
  return (
    <>
      <div className="aq-detail-section">
        <div className="aq-detail-section-label">Rationale</div>
        <p className="aq-detail-prose">
          {item.rationale?.trim() ||
            "No rationale has been captured for this decision yet."}
        </p>
      </div>

      <div className="aq-detail-section">
        <div className="aq-detail-section-label">Alternatives considered</div>
        {item.alternative_refs.length > 0 ? (
          <div className="aq-output-list">
            {item.alternative_refs.map((alternative) => (
              <article className="aq-inline-card" key={alternative.id}>
                <div className="aq-inline-card-head">
                  <span>{alternative.title}</span>
                  <span className="aq-mono aq-mute">{alternative.ref}</span>
                </div>
              </article>
            ))}
          </div>
        ) : (
          <p className="aq-detail-prose aq-mute">
            No explicit alternatives are linked to this decision.
          </p>
        )}
      </div>

      <div className="aq-detail-section" data-testid="decision-lineage">
        <div className="aq-detail-section-label">Supersedes chain</div>
        {lineageLoading ? (
          <p className="aq-detail-prose aq-mute">Loading lineage graph...</p>
        ) : lineageError ? (
          <p className="aq-detail-prose aq-mute">{lineageError}</p>
        ) : lineage ? (
          <div className="aq-lineage-grid">
            <LineageColumn
              label="Newer decisions"
              nodes={newerLineage}
              onSelect={onSelectLineage}
            />
            <LineageColumn
              label="Current"
              nodes={selectedLineageNode ? [selectedLineageNode] : []}
              onSelect={onSelectLineage}
            />
            <LineageColumn
              label="Superseded by this decision"
              nodes={olderLineage}
              onSelect={onSelectLineage}
            />
          </div>
        ) : (
          <p className="aq-detail-prose aq-mute">
            No supersedes chain is available for this decision yet.
          </p>
        )}
      </div>

      <div className="aq-detail-section">
        <div className="aq-detail-section-label">Linked Jobs</div>
        {item.linked_job_refs.length > 0 ? (
          <div className="aq-linked-jobs">
            {item.linked_job_refs.map((jobRef) => (
              <Link
                className="aq-prop-link aq-linked-job aq-mono"
                href={`/work?job=${encodeURIComponent(jobRef)}`}
                key={jobRef}
              >
                {jobRef}
              </Link>
            ))}
          </div>
        ) : (
          <p className="aq-detail-prose aq-mute">
            No linked Jobs are attached to this decision.
          </p>
        )}
      </div>

      <div className="aq-detail-section">
        <div className="aq-detail-section-label">Properties</div>
        <div className="aq-detail-props">
          <PropertyCard label="Ref" value={item.ref} />
          <PropertyCard label="Scope" value={item.scope} />
          <PropertyCard label="Decided" value={formatTimestamp(item.decided_at)} />
          <PropertyCard
            label="Actor"
            value={item.actor ? `@${item.actor}` : "system"}
          />
          <PropertyCard label="Status" value={item.status} />
          <PropertyCard label="Primary Job" value={item.primary_job_ref ?? "n/a"} />
        </div>
      </div>
    </>
  );
}

function LineageColumn({
  label,
  nodes,
  onSelect,
}: {
  label: string;
  nodes: DecisionLineageNode[];
  onSelect: (id: string) => void;
}) {
  return (
    <div className="aq-lineage-column">
      <p className="aq-lineage-column-title">{label}</p>
      {nodes.length > 0 ? (
        nodes.map((node) => (
          <button
            className="aq-lineage-node"
            key={node.id}
            onClick={() => onSelect(node.id)}
            type="button"
          >
            <span className="aq-lineage-node-ref aq-mono">{node.ref}</span>
            <span>{node.title}</span>
            <span className="aq-lineage-node-meta aq-mono aq-mute">
              {formatTimestamp(node.decided_at)} · {node.scope}
            </span>
          </button>
        ))
      ) : (
        <p className="aq-detail-prose aq-mute">No nodes</p>
      )}
    </div>
  );
}

function FilterGroup<T extends string>({
  counts,
  label,
  onSelect,
  options,
  selected,
}: {
  counts: Record<string, number>;
  label: string;
  onSelect: (value: T) => void;
  options: Array<{ value: T; label: string }>;
  selected: T;
}) {
  return (
    <div className="aq-filter-group">
      <span className="aq-filter-label">{label}</span>
      <div className="aq-filter-pills">
        {options.map((option) => (
          <button
            className={`aq-filter-pill ${selected === option.value ? "is-selected" : ""}`}
            key={option.value}
            onClick={() => onSelect(option.value)}
            type="button"
          >
            <span>{option.label}</span>
            <span className="aq-tab-count">{counts[option.value] ?? 0}</span>
          </button>
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

function buildFilterCounts({
  items,
  filterKey,
  options,
  scopeFilter,
  statusFilter,
  dateRangeFilter,
}: {
  items: DecisionItem[];
  filterKey: "scope" | "status";
  options: FilterValue[];
  scopeFilter: FilterValue;
  statusFilter: FilterValue;
  dateRangeFilter: DateRangeFilter;
}) {
  return Object.fromEntries(
    options.map((option) => [
      option,
      items.filter((item) =>
        matchesFilters(
          item,
          {
            scopeFilter,
            statusFilter,
            dateRangeFilter,
          },
          filterKey,
          option,
        ),
      ).length,
    ]),
  );
}

function buildDateRangeCounts({
  items,
  scopeFilter,
  statusFilter,
}: {
  items: DecisionItem[];
  scopeFilter: FilterValue;
  statusFilter: FilterValue;
}) {
  return Object.fromEntries(
    DATE_RANGE_OPTIONS.map((option) => [
      option.value,
      items.filter((item) =>
        matchesFilters(
          item,
          {
            scopeFilter,
            statusFilter,
            dateRangeFilter: option.value,
          },
          "dateRange",
          option.value,
        ),
      ).length,
    ]),
  );
}

function matchesFilters(
  item: DecisionItem,
  filters: {
    scopeFilter: FilterValue;
    statusFilter: FilterValue;
    dateRangeFilter: DateRangeFilter;
  },
  ignore: "scope" | "status" | "dateRange" | null = null,
  overrideValue?: string,
) {
  const scopeValue =
    overrideValue && ignore === "scope" ? overrideValue : filters.scopeFilter;
  const statusValue =
    overrideValue && ignore === "status" ? overrideValue : filters.statusFilter;
  const dateRangeValue =
    overrideValue && ignore === "dateRange"
      ? (overrideValue as DateRangeFilter)
      : filters.dateRangeFilter;

  if (ignore !== "scope" && scopeValue !== "all" && item.scope !== scopeValue) {
    return false;
  }

  if (
    ignore !== "status" &&
    statusValue !== "all" &&
    item.status !== statusValue
  ) {
    return false;
  }

  if (ignore !== "dateRange" && !matchesDateRange(item.decided_at, dateRangeValue)) {
    return false;
  }

  return true;
}

function matchesDateRange(value: string, filter: DateRangeFilter) {
  if (filter === "all") {
    return true;
  }

  const cutoff = Date.now();
  const timestamp = new Date(value).getTime();
  if (Number.isNaN(timestamp)) {
    return false;
  }

  const delta =
    filter === "24h"
      ? 24 * 60 * 60 * 1000
      : filter === "7d"
        ? 7 * 24 * 60 * 60 * 1000
        : 30 * 24 * 60 * 60 * 1000;

  return cutoff - timestamp <= delta;
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

function toDecisionPanelJob(item: DecisionItem): JobDetailPanelJob {
  return {
    ref: item.ref,
    title: item.title,
    task_type: `decision-${item.scope}`,
    status: item.status,
    priority: null,
    labels: decisionLabels(item),
    description: item.rationale,
    claimed_by_actor_id: item.actor,
    updated_at: item.decided_at,
    parent_ref: null,
    child_refs: [],
    depends_on: [],
    blocked_by: [],
    blocks: [],
  };
}

function decisionLabels(item: DecisionItem) {
  return [
    `scope:${item.scope}`,
    `status:${item.status}`,
    ...(item.project_slug ? [`project:${item.project_slug}`] : []),
  ];
}

function decisionRowElementId(ref: string) {
  return `aq-decision-row-${ref.replace(/[^a-zA-Z0-9_-]/g, "-")}`;
}
