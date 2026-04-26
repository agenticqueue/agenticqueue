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

type LearningScope = "task" | "project" | "global";
type LearningStatus = "active" | "superseded" | "expired";

type LearningItem = {
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

type LearningsResponse = {
  query: string;
  count: number;
  generated_at: string;
  items: LearningItem[];
};

type LearningsViewProps = {
  authToken: string;
};

type TierFilter = "all" | "1" | "2" | "3";
type ScopeFilter = "all" | LearningScope;
type StatusFilter = "all" | LearningStatus;

const TIER_OPTIONS: Array<{ value: TierFilter; label: string }> = [
  { value: "all", label: "All tiers" },
  { value: "1", label: "Tier 1" },
  { value: "2", label: "Tier 2" },
  { value: "3", label: "Tier 3" },
];

const SCOPE_OPTIONS: Array<{ value: ScopeFilter; label: string }> = [
  { value: "all", label: "All scopes" },
  { value: "task", label: "Task" },
  { value: "project", label: "Project" },
  { value: "global", label: "Global" },
];

const STATUS_OPTIONS: Array<{ value: StatusFilter; label: string }> = [
  { value: "all", label: "All statuses" },
  { value: "active", label: "Active" },
  { value: "superseded", label: "Superseded" },
  { value: "expired", label: "Expired" },
];

const LEARNINGS_SEARCH_DEBOUNCE_MS = 300;
const LEARNING_PANEL_CALLOUT =
  "Mutation stays outside the UI. Use `aq learning promote`, `aq learning supersede`, or MCP for lifecycle changes.";

export function LearningsView({ authToken }: LearningsViewProps) {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [items, setItems] = useState<LearningItem[]>([]);
  const [query, setQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [generatedAt, setGeneratedAt] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [tierFilter, setTierFilter] = useState<TierFilter>("all");
  const [scopeFilter, setScopeFilter] = useState<ScopeFilter>("all");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const focusReturnIdRef = useRef<string | null>(null);

  useEffect(() => {
    const timeout = window.setTimeout(() => {
      setDebouncedQuery(query.trim());
    }, LEARNINGS_SEARCH_DEBOUNCE_MS);
    return () => window.clearTimeout(timeout);
  }, [query]);

  useEffect(() => {
    const controller = new AbortController();

    setLoading(true);
    setError(null);

    void fetchLearnings(debouncedQuery, authToken, controller.signal)
      .then((payload) => {
        setItems(payload.items);
        setGeneratedAt(payload.generated_at);
        setSelectedId((current) =>
          current && payload.items.some((item) => item.id === current)
            ? current
            : null,
        );
      })
      .catch((requestError: unknown) => {
        if (controller.signal.aborted) {
          return;
        }
        setError(
          requestError instanceof Error
            ? requestError.message
            : "Failed to load learnings.",
        );
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setLoading(false);
        }
      });

    return () => controller.abort();
  }, [authToken, debouncedQuery]);

  const counts = useMemo(() => buildCounts(items), [items]);
  const filteredItems = useMemo(
    () =>
      items.filter((item) => {
        if (tierFilter !== "all" && String(item.tier) !== tierFilter) {
          return false;
        }
        if (scopeFilter !== "all" && item.scope !== scopeFilter) {
          return false;
        }
        if (statusFilter !== "all" && item.status !== statusFilter) {
          return false;
        }
        return true;
      }),
    [items, scopeFilter, statusFilter, tierFilter],
  );

  useEffect(() => {
    setSelectedId((current) =>
      current && filteredItems.some((item) => item.id === current)
        ? current
        : null,
    );
  }, [filteredItems]);

  const selectedItem = useMemo(
    () => filteredItems.find((item) => item.id === selectedId) ?? null,
    [filteredItems, selectedId],
  );

  const selectedPanelJob = useMemo(
    () => (selectedItem ? toLearningPanelJob(selectedItem) : null),
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

  return (
    <div className="aq-learnings-view">
      <div className="aq-content-head aq-content-head-pipelines">
        <div>
          <p className="aq-content-eyebrow">Phase 7 live view</p>
          <h1 className="aq-content-title">Learnings</h1>
        </div>
        <p className="aq-content-summary">
          Read-only browser over captured learnings. Search and filter the
          anti-repeat system here; promote, supersede, and expire through the
          CLI or MCP only.
        </p>
      </div>

      <div className="aq-pipelines-readonly">
        <span className="aq-pipelines-readonly-kicker">read-only</span>
        <span>
          Learnings stay visible in the shell, but mutation remains outside the
          UI. Use `aq learning` or MCP for promote, supersede, and lifecycle
          changes.
        </span>
      </div>

      <div className="aq-learnings-toolbar">
        <label className="aq-learnings-search">
          <span className="aq-auth-label">Search</span>
          <input
            aria-label="Search learnings"
            className="aq-auth-input aq-learnings-search-input"
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search title, context, evidence, or job refs"
            type="search"
            value={query}
          />
        </label>
        <div className="aq-learnings-toolbar-meta">
          <span className="aq-mono aq-mute">
            {filteredItems.length} visible · {items.length} matched
          </span>
          {generatedAt ? (
            <span className="aq-mono aq-mute">
              last sync {formatTimestamp(generatedAt)}
            </span>
          ) : null}
        </div>
      </div>

      <div className="aq-filter-stack">
        <FilterGroup
          counts={counts.tier}
          label="Tier"
          onSelect={setTierFilter}
          options={TIER_OPTIONS}
          selected={tierFilter}
        />
        <FilterGroup
          counts={counts.scope}
          label="Scope"
          onSelect={setScopeFilter}
          options={SCOPE_OPTIONS}
          selected={scopeFilter}
        />
        <FilterGroup
          counts={counts.status}
          label="Status"
          onSelect={setStatusFilter}
          options={STATUS_OPTIONS}
          selected={statusFilter}
        />
      </div>

      {loading ? (
        <div className="aq-pipelines-state">
          <p className="aq-auth-kicker">Loading learnings</p>
          <h2 className="aq-state-title">Building the learnings browser</h2>
          <p className="aq-state-copy">
            Hydrating learning records and task cross-references through the web
            proxy.
          </p>
        </div>
      ) : error ? (
        <div className="aq-pipelines-state is-error" role="alert">
          <p className="aq-auth-kicker">Load failure</p>
          <h2 className="aq-state-title">Learnings could not be loaded</h2>
          <p className="aq-state-copy">{error}</p>
        </div>
      ) : filteredItems.length === 0 ? (
        <div className="aq-empty aq-empty-pipelines">
          <span className="aq-mono aq-mute">
            {"// no learnings match the current query + filters"}
          </span>
        </div>
      ) : (
        <div className="aq-learnings-shell">
          <section className="aq-knowledge-list" aria-label="Learnings list">
            {filteredItems.map((item) => {
              const rowId = learningRowElementId(item.ref);
              return (
                <button
                  aria-pressed={selectedItem?.id === item.id}
                  className={`aq-knowledge-row ${
                    selectedItem?.id === item.id ? "is-selected" : ""
                  }`}
                  data-testid={`learning-row-${item.ref}`}
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
                    <span className="aq-knowledge-pipe">{item.scope}</span>
                    <span className="aq-knowledge-sep">·</span>
                    <span>tier {item.tier}</span>
                    <span className="aq-knowledge-sep">·</span>
                    <span>{item.confidence}</span>
                  </span>
                </button>
              );
            })}
          </section>
        </div>
      )}

      {selectedItem && selectedPanelJob ? (
        <JobDetailPanel
          callout={LEARNING_PANEL_CALLOUT}
          eyebrow="Selected learning"
          job={selectedPanelJob}
          onClose={closeSelectedItem}
          onSelect={() => undefined}
          open
          pipelineName={`Learnings ${selectedItem.scope}`}
          testId="learning-detail"
        >
          <LearningPanelSections item={selectedItem} />
        </JobDetailPanel>
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
            className={`aq-filter-pill ${
              selected === option.value ? "is-selected" : ""
            }`}
            key={option.value}
            onClick={() => onSelect(option.value)}
            type="button"
          >
            <span>{option.label}</span>
            <span className="aq-tab-count">
              {counts[option.value] ?? counts.all ?? 0}
            </span>
          </button>
        ))}
      </div>
    </div>
  );
}

async function fetchLearnings(
  query: string,
  authToken: string,
  signal: AbortSignal,
) {
  const response = await fetch(
    `/api/v1/learnings/search?query=${encodeURIComponent(query)}`,
    {
      headers: {
        Authorization: `Bearer ${authToken}`,
      },
      cache: "no-store",
      signal,
    },
  );

  const payload = (await response.json().catch(() => null)) as
    | LearningsResponse
    | { error?: string }
    | null;

  if (!response.ok || payload === null || !("items" in payload)) {
    throw new Error(
      payload && "error" in payload && typeof payload.error === "string"
        ? payload.error
        : "Learnings request failed.",
    );
  }

  return payload;
}

function buildCounts(items: LearningItem[]) {
  return {
    tier: {
      all: items.length,
      1: items.filter((item) => item.tier === 1).length,
      2: items.filter((item) => item.tier === 2).length,
      3: items.filter((item) => item.tier === 3).length,
    },
    scope: {
      all: items.length,
      task: items.filter((item) => item.scope === "task").length,
      project: items.filter((item) => item.scope === "project").length,
      global: items.filter((item) => item.scope === "global").length,
    },
    status: {
      all: items.length,
      active: items.filter((item) => item.status === "active").length,
      superseded: items.filter((item) => item.status === "superseded").length,
      expired: items.filter((item) => item.status === "expired").length,
    },
  };
}

function LearningPanelSections({ item }: { item: LearningItem }) {
  return (
    <>
      <div className="aq-detail-section">
        <div className="aq-detail-section-label">Context</div>
        <div className="aq-detail-copy">
          <p className="aq-detail-prose">
            <strong>What happened:</strong> {item.context.what_happened}
          </p>
          <p className="aq-detail-prose">
            <strong>What learned:</strong> {item.context.what_learned}
          </p>
          <p className="aq-detail-prose">
            <strong>Action rule:</strong> {item.context.action_rule}
          </p>
          <p className="aq-detail-prose">
            <strong>Applies when:</strong> {item.context.applies_when}
          </p>
          <p className="aq-detail-prose">
            <strong>Does not apply:</strong> {item.context.does_not_apply_when}
          </p>
        </div>
      </div>

      <div className="aq-detail-section">
        <div className="aq-detail-section-label">Evidence</div>
        {item.evidence.length > 0 ? (
          <ul className="aq-detail-list">
            {item.evidence.map((entry) => (
              <li className="aq-mono" key={entry}>
                {entry}
              </li>
            ))}
          </ul>
        ) : (
          <p className="aq-detail-prose aq-mute">No evidence attached.</p>
        )}
      </div>

      <div className="aq-detail-section">
        <div className="aq-detail-section-label">Applied in</div>
        {item.applied_in.length > 0 ? (
          <div className="aq-job-detail-relbuttons">
            {item.applied_in.map((entry) => (
              <Link
                className="aq-prop-link aq-mono"
                href={entry.href}
                key={entry.task_id}
              >
                {entry.ref}
              </Link>
            ))}
          </div>
        ) : (
          <p className="aq-detail-prose aq-mute">
            No linked job cross-references.
          </p>
        )}
      </div>

      <div className="aq-detail-section">
        <div className="aq-detail-section-label">Properties</div>
        <div className="aq-detail-props">
          <div className="aq-prop">
            <span className="aq-prop-k">Scope</span>
            <span className="aq-prop-v aq-mono">{item.scope}</span>
          </div>
          <div className="aq-prop">
            <span className="aq-prop-k">Tier</span>
            <span className="aq-prop-v aq-mono">{item.tier}</span>
          </div>
          <div className="aq-prop">
            <span className="aq-prop-k">Confidence</span>
            <span className="aq-prop-v aq-mono">{item.confidence}</span>
          </div>
          <div className="aq-prop">
            <span className="aq-prop-k">Status</span>
            <span className="aq-prop-v aq-mono">{item.status}</span>
          </div>
          <div className="aq-prop">
            <span className="aq-prop-k">Owner</span>
            <span className="aq-prop-v aq-mono">{item.owner ?? "unknown"}</span>
          </div>
          <div className="aq-prop">
            <span className="aq-prop-k">Review date</span>
            <span className="aq-prop-v aq-mono">
              {item.review_date ?? "n/a"}
            </span>
          </div>
        </div>
      </div>
    </>
  );
}

function toLearningPanelJob(item: LearningItem): JobDetailPanelJob {
  return {
    ref: item.ref,
    title: item.title,
    task_type: `learning-${item.scope}`,
    status: item.status,
    priority: item.tier,
    labels: learningLabels(item),
    description: item.context.what_learned,
    claimed_by_actor_id: item.owner,
    updated_at: item.last_applied,
    parent_ref: null,
    child_refs: [],
    depends_on: [],
    blocked_by: [],
    blocks: [],
  };
}

function learningLabels(item: LearningItem) {
  return [
    `scope:${item.scope}`,
    `tier:${item.tier}`,
    `confidence:${item.confidence}`,
    `status:${item.status}`,
  ];
}

function learningRowElementId(ref: string) {
  return `aq-learning-row-${ref.replace(/[^a-zA-Z0-9_-]/g, "-")}`;
}

function formatTimestamp(value: string) {
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
  }).format(new Date(value));
}
