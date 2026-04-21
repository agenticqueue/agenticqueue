"use client";

import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";

type WorkStatus = "running" | "failed" | "review" | "queued" | "blocked" | "done";
type WorkActivitySource = "task" | "run" | "decision" | "artifact";
type FilterValue = "all" | string;
type DateRangeFilter = "all" | "24h" | "7d" | "30d";
type DetailTab = "overview" | "outputs" | "activity" | "properties";

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
  source: WorkActivitySource;
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

type WorkViewProps = {
  authToken: string;
};

const PAGE_SIZE = 50;

const DATE_RANGE_OPTIONS: Array<{ value: DateRangeFilter; label: string }> = [
  { value: "all", label: "All time" },
  { value: "24h", label: "Last 24h" },
  { value: "7d", label: "Last 7d" },
  { value: "30d", label: "Last 30d" },
];

const DETAIL_TABS: Array<{ value: DetailTab; label: string }> = [
  { value: "overview", label: "Overview" },
  { value: "outputs", label: "Outputs" },
  { value: "activity", label: "Activity log" },
  { value: "properties", label: "Properties" },
];

const STATUS_ORDER: WorkStatus[] = [
  "running",
  "failed",
  "review",
  "blocked",
  "queued",
  "done",
];

export function WorkView({ authToken }: WorkViewProps) {
  const searchParams = useSearchParams();
  const initialRef = searchParams.get("job");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [items, setItems] = useState<WorkItem[]>([]);
  const [generatedAt, setGeneratedAt] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<DetailTab>("overview");
  const [showShortcuts, setShowShortcuts] = useState(false);
  const [actorFilter, setActorFilter] = useState<FilterValue>("all");
  const [pipelineFilter, setPipelineFilter] = useState<FilterValue>("all");
  const [statusFilter, setStatusFilter] = useState<FilterValue>("all");
  const [dateRangeFilter, setDateRangeFilter] = useState<DateRangeFilter>("all");
  const [pageStart, setPageStart] = useState(0);
  const [refreshNonce, setRefreshNonce] = useState(0);

  useEffect(() => {
    let cancelled = false;
    let inFlight = false;
    const controllers = new Set<AbortController>();

    async function load() {
      if (inFlight) {
        return;
      }

      inFlight = true;
      const controller = new AbortController();
      controllers.add(controller);

      try {
        setLoading((current) => current && items.length === 0);
        setError(null);

        const response = await fetch("/api/v1/work", {
          headers: {
            Authorization: `Bearer ${authToken}`,
          },
          cache: "no-store",
          signal: controller.signal,
        });

        const payload = (await response.json().catch(() => null)) as
          | WorkResponse
          | { error?: string }
          | null;

        if (!response.ok || payload === null || !("items" in payload)) {
          throw new Error(
            payload && "error" in payload && typeof payload.error === "string"
              ? payload.error
              : "Work request failed.",
          );
        }

        if (cancelled) {
          return;
        }

        setItems(payload.items);
        setGeneratedAt(payload.generated_at);
      } catch (requestError: unknown) {
        if (cancelled || controller.signal.aborted) {
          return;
        }

        setError(
          requestError instanceof Error
            ? requestError.message
            : "Failed to load work view.",
        );
      } finally {
        inFlight = false;
        controllers.delete(controller);
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
      controllers.forEach((controller) => controller.abort());
      window.clearInterval(intervalId);
      document.removeEventListener("visibilitychange", handleVisibility);
    };
  }, [authToken, items.length, refreshNonce]);

  const actorOptions = useMemo(
    () =>
      ["all", ...new Set(items.map((item) => item.actor ?? "unclaimed"))] as FilterValue[],
    [items],
  );
  const pipelineOptions = useMemo(
    () => ["all", ...new Set(items.map((item) => item.pipeline_slug))] as FilterValue[],
    [items],
  );
  const statusOptions = useMemo(
    () => ["all", ...STATUS_ORDER.filter((status) => items.some((item) => item.status === status))] as FilterValue[],
    [items],
  );

  const filteredItems = useMemo(
    () =>
      items.filter((item) =>
        matchesFilters(item, {
          actorFilter,
          pipelineFilter,
          statusFilter,
          dateRangeFilter,
        }),
      ),
    [actorFilter, dateRangeFilter, items, pipelineFilter, statusFilter],
  );

  const visibleItems = useMemo(
    () => filteredItems.slice(pageStart, pageStart + PAGE_SIZE),
    [filteredItems, pageStart],
  );

  const selectedItem = useMemo(
    () => filteredItems.find((item) => item.id === selectedId) ?? null,
    [filteredItems, selectedId],
  );

  const actorCounts = useMemo(
    () =>
      buildFilterCounts({
        items,
        options: actorOptions,
        filterKey: "actor",
        actorFilter,
        pipelineFilter,
        statusFilter,
        dateRangeFilter,
      }),
    [actorFilter, actorOptions, dateRangeFilter, items, pipelineFilter, statusFilter],
  );

  const pipelineCounts = useMemo(
    () =>
      buildFilterCounts({
        items,
        options: pipelineOptions,
        filterKey: "pipeline",
        actorFilter,
        pipelineFilter,
        statusFilter,
        dateRangeFilter,
      }),
    [actorFilter, dateRangeFilter, items, pipelineFilter, pipelineOptions, statusFilter],
  );

  const statusCounts = useMemo(
    () =>
      buildFilterCounts({
        items,
        options: statusOptions,
        filterKey: "status",
        actorFilter,
        pipelineFilter,
        statusFilter,
        dateRangeFilter,
      }),
    [actorFilter, dateRangeFilter, items, pipelineFilter, statusFilter, statusOptions],
  );

  const dateRangeCounts = useMemo(
    () =>
      buildDateRangeCounts({
        items,
        actorFilter,
        pipelineFilter,
        statusFilter,
      }),
    [actorFilter, items, pipelineFilter, statusFilter],
  );

  useEffect(() => {
    setPageStart(0);
  }, [actorFilter, dateRangeFilter, pipelineFilter, statusFilter]);

  useEffect(() => {
    setActiveTab("overview");
  }, [selectedId]);

  useEffect(() => {
    if (!initialRef) {
      return;
    }

    const nextItem = filteredItems.find((item) => item.ref === initialRef);
    if (nextItem) {
      setSelectedId(nextItem.id);
    }
  }, [filteredItems, initialRef]);

  useEffect(() => {
    if (!selectedId) {
      return;
    }

    const selectedIndex = filteredItems.findIndex((item) => item.id === selectedId);
    if (selectedIndex === -1) {
      setSelectedId(null);
      return;
    }

    const nextPageStart = Math.floor(selectedIndex / PAGE_SIZE) * PAGE_SIZE;
    if (nextPageStart !== pageStart) {
      setPageStart(nextPageStart);
    }
  }, [filteredItems, pageStart, selectedId]);

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

      if (event.key === "?" || (event.key === "/" && event.shiftKey)) {
        event.preventDefault();
        setShowShortcuts((current) => !current);
        return;
      }

      if (event.key === "Escape") {
        setShowShortcuts(false);
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

      if (!selectedId) {
        setSelectedId(
          filteredItems[event.key === "ArrowUp" || event.key === "ArrowLeft" ? filteredItems.length - 1 : 0]?.id ??
            null,
        );
        return;
      }

      const currentIndex = filteredItems.findIndex((item) => item.id === selectedId);
      const delta = event.key === "ArrowUp" || event.key === "ArrowLeft" ? -1 : 1;
      const nextIndex =
        currentIndex === -1
          ? 0
          : (currentIndex + delta + filteredItems.length) % filteredItems.length;

      setSelectedId(filteredItems[nextIndex]?.id ?? null);
    }

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [filteredItems, selectedId]);

  const totalPages = Math.max(Math.ceil(filteredItems.length / PAGE_SIZE), 1);
  const currentPage = Math.floor(pageStart / PAGE_SIZE) + 1;

  return (
    <div className="aq-work-view">
      <div className="aq-content-head aq-content-head-pipelines">
        <div>
          <p className="aq-content-eyebrow">Phase 7 live view</p>
          <h1 className="aq-content-title">Work</h1>
        </div>
        <p className="aq-content-summary">
          Cross-pipeline job table with a right-side detail panel for outputs,
          activity, and properties. The shell stays read-only; use CLI, REST,
          or MCP for all mutations.
        </p>
      </div>

      <div className="aq-pipelines-readonly">
        <span className="aq-pipelines-readonly-kicker">read-only</span>
        <span>
          Activity reflects real task, run, artifact, and decision data.
          Manual refresh is available, and the view auto-polls every 30 seconds
          while this tab stays visible.
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
          <span className="aq-mono aq-mute">press ? for shortcuts</span>
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
          counts={actorCounts}
          label="Actor"
          onSelect={setActorFilter}
          options={actorOptions.map((option) => ({
            label: option === "all" ? "All actors" : option,
            value: option,
          }))}
          selected={actorFilter}
        />
        <FilterGroup
          counts={pipelineCounts}
          label="Pipeline"
          onSelect={setPipelineFilter}
          options={pipelineOptions.map((option) => ({
            label: option === "all" ? "All pipelines" : option,
            value: option,
          }))}
          selected={pipelineFilter}
        />
        <FilterGroup
          counts={statusCounts}
          label="Status"
          onSelect={setStatusFilter}
          options={statusOptions.map((option) => ({
            label: option === "all" ? "All statuses" : option,
            value: option,
          }))}
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
          <p className="aq-auth-kicker">Loading work</p>
          <h2 className="aq-state-title">Building the cross-pipeline queue lens</h2>
          <p className="aq-state-copy">
            Joining tasks, runs, artifacts, decisions, and edge metadata into
            one read-only work table.
          </p>
        </div>
      ) : error ? (
        <div className="aq-pipelines-state is-error" role="alert">
          <p className="aq-auth-kicker">Load failure</p>
          <h2 className="aq-state-title">Work could not be loaded</h2>
          <p className="aq-state-copy">{error}</p>
        </div>
      ) : filteredItems.length === 0 ? (
        <div className="aq-empty aq-empty-pipelines">
          <span className="aq-mono aq-mute">
            {"// no work items match the current actor + pipeline + status + date filters"}
          </span>
        </div>
      ) : (
        <div className="aq-work-shell">
          <section className="aq-table-work" aria-label="Work table">
            <div className="aq-work-head aq-mono aq-mute">
              <div>ref</div>
              <div>title</div>
              <div>pipeline</div>
              <div>actor</div>
              <div>claimed</div>
              <div>closed</div>
              <div>status</div>
            </div>

            {visibleItems.map((item) => (
              <button
                className={`aq-table-row ${selectedItem?.id === item.id ? "is-selected" : ""}`}
                data-testid={`work-row-${item.ref}`}
                key={item.id}
                onClick={() => setSelectedId(item.id)}
                type="button"
              >
                <div className="aq-mono">{item.ref}</div>
                <div className="aq-table-title">
                  <span>{item.title}</span>
                  <span className="aq-table-subtitle">{item.task_type}</span>
                </div>
                <div className="aq-mute">{item.pipeline}</div>
                <div className="aq-mono aq-mute">
                  {item.actor ? `@${item.actor}` : "unclaimed"}
                </div>
                <div className="aq-mono aq-mute">{formatNullableTimestamp(item.claimed_at)}</div>
                <div className="aq-mono aq-mute">{formatNullableTimestamp(item.closed_at)}</div>
                <div>
                  <ToneChip label={item.status} tone={statusTone(item.status)} />
                </div>
              </button>
            ))}

            <div className="aq-work-pagination">
              <span className="aq-mono aq-mute">
                page {currentPage} / {totalPages} · rows {pageStart + 1}-
                {Math.min(pageStart + PAGE_SIZE, filteredItems.length)}
              </span>
              <div className="aq-work-pagination-actions">
                <button
                  className="aq-secondary-button"
                  disabled={pageStart === 0}
                  onClick={() => setPageStart((current) => Math.max(current - PAGE_SIZE, 0))}
                  type="button"
                >
                  Previous 50
                </button>
                <button
                  className="aq-secondary-button"
                  disabled={pageStart + PAGE_SIZE >= filteredItems.length}
                  onClick={() =>
                    setPageStart((current) =>
                      Math.min(current + PAGE_SIZE, Math.max(filteredItems.length - PAGE_SIZE, 0)),
                    )
                  }
                  type="button"
                >
                  Next 50
                </button>
              </div>
            </div>
          </section>

          {selectedItem ? (
            <aside className="aq-detail" data-testid="work-detail">
              <div className="aq-detail-head">
                <div>
                  <p className="aq-auth-kicker">Selected job</p>
                  <h2 className="aq-detail-title">{selectedItem.title}</h2>
                </div>
                <div className="aq-detail-status-row">
                  <ToneChip label={selectedItem.status} tone={statusTone(selectedItem.status)} />
                  <span className="aq-mono aq-detail-ref">{selectedItem.ref}</span>
                </div>
              </div>

              <div className="aq-detail-copy">
                <p className="aq-detail-prose">
                  <strong>Pipeline:</strong> {selectedItem.pipeline}
                </p>
                <p className="aq-detail-prose">
                  <strong>Actor:</strong>{" "}
                  {selectedItem.actor ? `@${selectedItem.actor}` : "unclaimed"}
                </p>
              </div>

              <div className="aq-tab-strip" role="tablist" aria-label="Work detail tabs">
                {DETAIL_TABS.map((tab) => (
                  <button
                    aria-selected={activeTab === tab.value}
                    className={`aq-tab-button ${activeTab === tab.value ? "is-selected" : ""}`}
                    key={tab.value}
                    onClick={() => setActiveTab(tab.value)}
                    role="tab"
                    type="button"
                  >
                    {tab.label}
                  </button>
                ))}
              </div>

              {activeTab === "overview" ? (
                <div className="aq-tab-panel">
                  <div className="aq-detail-section">
                    <div className="aq-detail-section-label">Summary</div>
                    <p className="aq-detail-prose">
                      {selectedItem.description?.trim() ||
                        "No narrative description is attached to this job yet."}
                    </p>
                  </div>

                  <div className="aq-detail-section">
                    <div className="aq-detail-section-label">Decision notes</div>
                    {selectedItem.decisions.length > 0 ? (
                      <div className="aq-detail-copy">
                        {selectedItem.decisions.slice(0, 3).map((decision) => (
                          <article className="aq-inline-card" key={decision.id}>
                            <div className="aq-inline-card-head">
                              <span>{decision.summary}</span>
                              <span className="aq-mono aq-mute">
                                {formatTimestamp(decision.decided_at)}
                              </span>
                            </div>
                            <p className="aq-detail-prose">
                              {decision.rationale ?? "No rationale captured."}
                            </p>
                          </article>
                        ))}
                      </div>
                    ) : (
                      <p className="aq-detail-prose aq-mute">
                        No decision records are attached yet.
                      </p>
                    )}
                  </div>

                  <div className="aq-detail-props">
                    <PropertyCard
                      label="Claimed"
                      value={formatNullableTimestamp(selectedItem.claimed_at)}
                    />
                    <PropertyCard
                      label="Closed"
                      value={formatNullableTimestamp(selectedItem.closed_at)}
                    />
                    <PropertyCard label="Priority" value={String(selectedItem.priority)} />
                    <PropertyCard label="Raw state" value={selectedItem.raw_state} />
                  </div>
                </div>
              ) : null}

              {activeTab === "outputs" ? (
                <div className="aq-tab-panel">
                  <div className="aq-detail-section">
                    <div className="aq-detail-section-label">Artifacts</div>
                    {selectedItem.outputs.length > 0 ? (
                      <div className="aq-output-list">
                        {selectedItem.outputs.map((output) => (
                          <article className="aq-output-row" key={output.id}>
                            <div>
                              <p className="aq-row-title">{output.label}</p>
                              <p className="aq-row-body">{output.uri}</p>
                            </div>
                            <div className="aq-output-meta">
                              <ToneChip label={output.kind} tone="info" />
                              <span className="aq-mono aq-mute">
                                {formatTimestamp(output.created_at)}
                              </span>
                            </div>
                          </article>
                        ))}
                      </div>
                    ) : (
                      <p className="aq-detail-prose aq-mute">
                        No produced artifacts recorded yet.
                      </p>
                    )}
                  </div>
                </div>
              ) : null}

              {activeTab === "activity" ? (
                <div className="aq-tab-panel">
                  <div className="aq-detail-section">
                    <div className="aq-detail-section-label">Activity log</div>
                    <div className="aq-activity-list">
                      {selectedItem.activity.map((entry) => (
                        <article className="aq-activity-entry" key={entry.id}>
                          <div className="aq-inline-card-head">
                            <span>{entry.label}</span>
                            <span className="aq-mono aq-mute">
                              {formatTimestamp(entry.happened_at)}
                            </span>
                          </div>
                          <p className="aq-detail-prose">{entry.summary}</p>
                          <div className="aq-activity-footer">
                            <ToneChip label={entry.source} tone="mute" />
                            {entry.state ? (
                              <ToneChip label={entry.state} tone={statusTone(entry.state)} />
                            ) : null}
                            {entry.command ? (
                              <span className="aq-mono aq-activity-command">
                                {entry.command}
                              </span>
                            ) : (
                              <span className="aq-mono aq-mute">
                                command metadata unavailable
                              </span>
                            )}
                          </div>
                        </article>
                      ))}
                    </div>
                  </div>
                </div>
              ) : null}

              {activeTab === "properties" ? (
                <div className="aq-tab-panel">
                  <div className="aq-detail-props">
                    <PropertyCard
                      label="Created"
                      value={formatTimestamp(selectedItem.created_at)}
                    />
                    <PropertyCard
                      label="Updated"
                      value={formatTimestamp(selectedItem.updated_at)}
                    />
                    <PropertyCard label="Task type" value={selectedItem.task_type} />
                    <PropertyCard label="Pipeline slug" value={selectedItem.pipeline_slug} />
                  </div>

                  <RelationSection label="Parent" refs={asRefs(selectedItem.parent_ref)} />
                  <RelationSection
                    label="Depends on"
                    refs={selectedItem.dependency_refs}
                  />
                  <RelationSection
                    label="Blocked by"
                    refs={selectedItem.blocked_by_refs}
                  />
                  <RelationSection label="Blocks" refs={selectedItem.block_refs} />
                  <RelationSection label="Children" refs={selectedItem.child_refs} />

                  {selectedItem.labels.length > 0 ? (
                    <div className="aq-detail-section">
                      <div className="aq-detail-section-label">Labels</div>
                      <div className="aq-job-detail-labels">
                        {selectedItem.labels.map((label) => (
                          <ToneChip key={label} label={label} tone="mute" />
                        ))}
                      </div>
                    </div>
                  ) : null}
                </div>
              ) : null}

              <div className="aq-job-detail-callout">
                <span className="aq-mono aq-mute">
                  Writes are disabled here. Use `aq`, REST, or MCP for task actions.
                </span>
              </div>
            </aside>
          ) : (
            <aside className="aq-detail aq-detail-empty">
              <p className="aq-auth-kicker">No job selected</p>
              <h2 className="aq-detail-title">Choose a row to open the detail panel</h2>
              <p className="aq-detail-prose">
                Click any row or use the arrow keys to move selection. Press `?`
                to open the keyboard shortcut overlay.
              </p>
            </aside>
          )}
        </div>
      )}

      {showShortcuts ? (
        <div className="aq-shortcuts-overlay" data-testid="work-shortcuts">
          <div className="aq-shortcuts-card">
            <div className="aq-inline-card-head">
              <span>Keyboard shortcuts</span>
              <button
                className="aq-secondary-button"
                onClick={() => setShowShortcuts(false)}
                type="button"
              >
                Close
              </button>
            </div>
            <div className="aq-shortcuts-grid">
              <ShortcutRow combo="Arrow Up / Left" description="Select previous row" />
              <ShortcutRow combo="Arrow Down / Right" description="Select next row" />
              <ShortcutRow combo="?" description="Toggle this help overlay" />
              <ShortcutRow combo="Escape" description="Close the overlay" />
            </div>
          </div>
        </div>
      ) : null}
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
            <span className="aq-nav-count">{counts[option.value] ?? 0}</span>
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

function RelationSection({ label, refs }: { label: string; refs: string[] }) {
  if (refs.length === 0) {
    return null;
  }

  return (
    <div className="aq-detail-section">
      <div className="aq-detail-section-label">{label}</div>
      <div className="aq-job-detail-relbuttons">
        {refs.map((ref) => (
          <span className="aq-prop-link aq-mono" key={`${label}-${ref}`}>
            {ref}
          </span>
        ))}
      </div>
    </div>
  );
}

function ShortcutRow({
  combo,
  description,
}: {
  combo: string;
  description: string;
}) {
  return (
    <div className="aq-shortcut-row">
      <span className="aq-shortcut-combo">{combo}</span>
      <span className="aq-detail-prose">{description}</span>
    </div>
  );
}

function ToneChip({
  label,
  tone,
}: {
  label: string;
  tone: "ok" | "info" | "warn" | "danger" | "mute";
}) {
  return <span className={`aq-tone aq-tone-${tone}`}>{label}</span>;
}

function buildFilterCounts({
  items,
  options,
  filterKey,
  actorFilter,
  pipelineFilter,
  statusFilter,
  dateRangeFilter,
}: {
  items: WorkItem[];
  options: FilterValue[];
  filterKey: "actor" | "pipeline" | "status";
  actorFilter: FilterValue;
  pipelineFilter: FilterValue;
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
            actorFilter,
            pipelineFilter,
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
  actorFilter,
  pipelineFilter,
  statusFilter,
}: {
  items: WorkItem[];
  actorFilter: FilterValue;
  pipelineFilter: FilterValue;
  statusFilter: FilterValue;
}) {
  return Object.fromEntries(
    DATE_RANGE_OPTIONS.map((option) => [
      option.value,
      items.filter((item) =>
        matchesFilters(
          item,
          {
            actorFilter,
            pipelineFilter,
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
  item: WorkItem,
  filters: {
    actorFilter: FilterValue;
    pipelineFilter: FilterValue;
    statusFilter: FilterValue;
    dateRangeFilter: DateRangeFilter;
  },
  ignore: "actor" | "pipeline" | "status" | "dateRange" | null = null,
  overrideValue?: string,
) {
  const actorValue = overrideValue && ignore === "actor" ? overrideValue : filters.actorFilter;
  const pipelineValue =
    overrideValue && ignore === "pipeline" ? overrideValue : filters.pipelineFilter;
  const statusValue = overrideValue && ignore === "status" ? overrideValue : filters.statusFilter;
  const dateRangeValue =
    overrideValue && ignore === "dateRange"
      ? (overrideValue as DateRangeFilter)
      : filters.dateRangeFilter;

  if (
    ignore !== "actor" &&
    actorValue !== "all" &&
    (item.actor ?? "unclaimed") !== actorValue
  ) {
    return false;
  }

  if (ignore !== "pipeline" && pipelineValue !== "all" && item.pipeline_slug !== pipelineValue) {
    return false;
  }

  if (ignore !== "status" && statusValue !== "all" && item.status !== statusValue) {
    return false;
  }

  if (ignore !== "dateRange" && !matchesDateRange(item.updated_at, dateRangeValue)) {
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

function statusTone(status: WorkStatus) {
  if (status === "done") {
    return "ok" as const;
  }
  if (status === "failed") {
    return "danger" as const;
  }
  if (status === "review" || status === "blocked") {
    return "warn" as const;
  }
  if (status === "running") {
    return "info" as const;
  }
  return "mute" as const;
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

function formatNullableTimestamp(value: string | null) {
  return value ? formatTimestamp(value) : "--";
}

function asRefs(value: string | null) {
  return value ? [value] : [];
}
