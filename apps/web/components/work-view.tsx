"use client";

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useSearchParams } from "next/navigation";

import {
  JobDetailPanel,
  type JobDetailPanelJob,
} from "@/components/job-detail-panel";

type WorkStatus = "running" | "failed" | "review" | "queued" | "blocked" | "done";
type WorkActivitySource = "task" | "run" | "decision" | "artifact";
type WorkStatusTab = "all" | "running" | "failed" | "review" | "queued" | "done";

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

const STATUS_TABS: Array<{ value: WorkStatusTab; label: string }> = [
  { value: "all", label: "all" },
  { value: "running", label: "running" },
  { value: "failed", label: "failed" },
  { value: "review", label: "review" },
  { value: "queued", label: "queued" },
  { value: "done", label: "done" },
];

const WORK_REFRESH_INTERVAL_MS = 30_000;

export function WorkView({ authToken }: WorkViewProps) {
  const searchParams = useSearchParams();
  const initialRef = searchParams.get("job");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [items, setItems] = useState<WorkItem[]>([]);
  const [generatedAt, setGeneratedAt] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [showShortcuts, setShowShortcuts] = useState(false);
  const [statusFilter, setStatusFilter] = useState<WorkStatusTab>("all");
  const [pageStart, setPageStart] = useState(0);
  const [refreshNonce, setRefreshNonce] = useState(0);
  const focusReturnIdRef = useRef<string | null>(null);

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
    }, WORK_REFRESH_INTERVAL_MS);

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

  const statusCounts = useMemo(
    () =>
      Object.fromEntries(
        STATUS_TABS.map((tab) => [
          tab.value,
          tab.value === "all"
            ? items.length
            : items.filter((item) => item.status === tab.value).length,
        ]),
      ) as Record<WorkStatusTab, number>,
    [items],
  );

  const filteredItems = useMemo(
    () =>
      statusFilter === "all"
        ? items
        : items.filter((item) => item.status === statusFilter),
    [items, statusFilter],
  );

  const visibleItems = useMemo(
    () => filteredItems.slice(pageStart, pageStart + PAGE_SIZE),
    [filteredItems, pageStart],
  );

  const selectedItem = useMemo(
    () => filteredItems.find((item) => item.id === selectedId) ?? null,
    [filteredItems, selectedId],
  );

  const selectedPanelJob = useMemo(
    () => (selectedItem ? toJobDetailPanelJob(selectedItem) : null),
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

  const selectRelatedJob = useCallback(
    (ref: string) => {
      const nextItem = items.find((item) => item.ref === ref);
      if (!nextItem) {
        return;
      }

      if (statusFilter !== "all" && nextItem.status !== statusFilter) {
        setStatusFilter("all");
      }
      setSelectedId(nextItem.id);
    },
    [items, statusFilter],
  );

  useEffect(() => {
    setPageStart(0);
  }, [statusFilter]);

  useEffect(() => {
    if (!initialRef) {
      return;
    }

    const nextItem = items.find((item) => item.ref === initialRef);
    if (nextItem) {
      setSelectedId(nextItem.id);
      if (statusFilter !== "all" && nextItem.status !== statusFilter) {
        setStatusFilter("all");
      }
    }
  }, [initialRef, items, statusFilter]);

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
        if (showShortcuts) {
          setShowShortcuts(false);
          return;
        }
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
  }, [
    closeSelectedItem,
    filteredItems,
    selectedId,
    showShortcuts,
  ]);

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
          Cross-pipeline job table with the same shared side panel and subtab
          pattern used by the canonical execution views. The shell stays
          read-only; use CLI, REST, or MCP for all mutations.
        </p>
      </div>

      <div className="aq-pipelines-readonly">
        <span className="aq-pipelines-readonly-kicker">read-only</span>
        <span>
          Activity reflects real task, run, artifact, and decision data. Manual
          refresh is available, and the view auto-polls every 30 seconds while
          this tab stays visible.
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

      <div className="aq-subtabs" role="tablist" aria-label="Work status">
        {STATUS_TABS.map((tab) => (
          <button
            aria-selected={statusFilter === tab.value}
            className={`aq-subtab ${statusFilter === tab.value ? "is-active" : ""}`}
            data-testid={`work-subtab-${tab.value}`}
            key={tab.value}
            onClick={() => setStatusFilter(tab.value)}
            role="tab"
            type="button"
          >
            <span className="aq-subtab-label">{tab.label}</span>
            <span className="aq-subtab-count">{statusCounts[tab.value]}</span>
          </button>
        ))}
        <div className="aq-subtab-hint" aria-hidden="true" />
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
            {"// no work items match the selected status subtab"}
          </span>
        </div>
      ) : (
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

          {visibleItems.map((item) => {
            const rowId = workRowElementId(item.ref);
            return (
              <button
                aria-pressed={selectedItem?.id === item.id}
                className={`aq-table-row ${selectedItem?.id === item.id ? "is-selected" : ""}`}
                data-testid={`work-row-${item.ref}`}
                id={rowId}
                key={item.id}
                onClick={() => selectItem(item.id, rowId)}
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
                <div className="aq-mono aq-mute">
                  {formatNullableTimestamp(item.claimed_at)}
                </div>
                <div className="aq-mono aq-mute">
                  {formatNullableTimestamp(item.closed_at)}
                </div>
                <div>
                  <ToneChip label={item.status} tone={statusTone(item.status)} />
                </div>
              </button>
            );
          })}

          <div className="aq-work-pagination">
            <span className="aq-mono aq-mute">
              page {currentPage} / {totalPages} · rows {pageStart + 1}-
              {Math.min(pageStart + PAGE_SIZE, filteredItems.length)}
            </span>
            <div className="aq-work-pagination-actions">
              <button
                className="aq-secondary-button"
                disabled={pageStart === 0}
                onClick={() =>
                  setPageStart((current) => Math.max(current - PAGE_SIZE, 0))
                }
                type="button"
              >
                Previous 50
              </button>
              <button
                className="aq-secondary-button"
                disabled={pageStart + PAGE_SIZE >= filteredItems.length}
                onClick={() =>
                  setPageStart((current) =>
                    Math.min(
                      current + PAGE_SIZE,
                      Math.max(filteredItems.length - PAGE_SIZE, 0),
                    ),
                  )
                }
                type="button"
              >
                Next 50
              </button>
            </div>
          </div>
        </section>
      )}

      {selectedItem && selectedPanelJob ? (
        <JobDetailPanel
          job={selectedPanelJob}
          onClose={closeSelectedItem}
          onSelect={selectRelatedJob}
          open
          pipelineName={selectedItem.pipeline}
          testId="work-detail"
        />
      ) : null}

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
              <ShortcutRow combo="Escape" description="Close the open job panel" />
              <ShortcutRow combo="?" description="Toggle this help overlay" />
            </div>
          </div>
        </div>
      ) : null}
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

function toJobDetailPanelJob(item: WorkItem): JobDetailPanelJob {
  return {
    ref: item.ref,
    title: item.title,
    task_type: item.task_type,
    status: item.status,
    priority: item.priority,
    labels: item.labels,
    description: item.description,
    claimed_by_actor_id: item.actor,
    updated_at: item.updated_at,
    parent_ref: item.parent_ref,
    child_refs: item.child_refs,
    depends_on: item.dependency_refs.map((ref) => ({ ref })),
    blocked_by: item.blocked_by_refs.map((ref) => ({ ref })),
    blocks: item.block_refs.map((ref) => ({ ref })),
  };
}

function workRowElementId(ref: string) {
  return `aq-work-row-${ref.replace(/[^a-zA-Z0-9_-]/g, "-")}`;
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
