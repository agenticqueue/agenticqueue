"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

type LearningScope = "task" | "project" | "global";
type LearningStatus = "active" | "superseded" | "expired";
type LearningTone = "ok" | "info" | "warn" | "danger" | "mute";

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

export function LearningsView() {
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

  useEffect(() => {
    const timeout = window.setTimeout(() => {
      setDebouncedQuery(query.trim());
    }, 300);
    return () => window.clearTimeout(timeout);
  }, [query]);

  useEffect(() => {
    const controller = new AbortController();

    setLoading(true);
    setError(null);

    void fetchLearnings(debouncedQuery, controller.signal)
      .then((payload) => {
        setItems(payload.items);
        setGeneratedAt(payload.generated_at);
        setSelectedId((current) =>
          current && payload.items.some((item) => item.id === current)
            ? current
            : payload.items[0]?.id ?? null,
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
  }, [debouncedQuery]);

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
        : filteredItems[0]?.id ?? null,
    );
  }, [filteredItems]);

  const selectedItem =
    filteredItems.find((item) => item.id === selectedId) ?? filteredItems[0] ?? null;

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
          <section className="aq-learnings-table" aria-label="Learnings table">
            <div className="aq-learnings-head aq-mono aq-mute">
              <div>ref</div>
              <div>title</div>
              <div>scope</div>
              <div>tier</div>
              <div>confidence</div>
              <div>last applied</div>
              <div>status</div>
            </div>

            {filteredItems.map((item) => (
              <button
                className={`aq-learning-row ${
                  selectedItem?.id === item.id ? "is-selected" : ""
                }`}
                data-testid={`learning-row-${item.ref}`}
                key={item.id}
                onClick={() => setSelectedId(item.id)}
                type="button"
              >
                <div className="aq-learning-cell aq-mono">{item.ref}</div>
                <div className="aq-learning-cell aq-learning-title">
                  {item.title}
                </div>
                <div className="aq-learning-cell aq-mono">{item.scope}</div>
                <div className="aq-learning-cell aq-mono">tier {item.tier}</div>
                <div className="aq-learning-cell">
                  <ToneChip
                    label={item.confidence}
                    tone={confidenceTone(item.confidence)}
                  />
                </div>
                <div className="aq-learning-cell aq-mono">
                  {formatTimestamp(item.last_applied)}
                </div>
                <div className="aq-learning-cell">
                  <ToneChip label={item.status} tone={statusTone(item.status)} />
                </div>
              </button>
            ))}
          </section>

          {selectedItem ? (
            <aside className="aq-detail" data-testid="learning-detail">
              <div className="aq-detail-head">
                <div>
                  <p className="aq-auth-kicker">Selected learning</p>
                  <h2 className="aq-detail-title">{selectedItem.title}</h2>
                </div>
                <div className="aq-detail-status-row">
                  <ToneChip
                    label={`tier ${selectedItem.tier}`}
                    tone={tierTone(selectedItem.tier)}
                  />
                  <ToneChip
                    label={selectedItem.confidence}
                    tone={confidenceTone(selectedItem.confidence)}
                  />
                  <ToneChip
                    label={selectedItem.status}
                    tone={statusTone(selectedItem.status)}
                  />
                </div>
              </div>

              <div className="aq-detail-ref aq-mono aq-mute">{selectedItem.ref}</div>

              <div className="aq-detail-section">
                <div className="aq-detail-section-label">Context</div>
                <div className="aq-detail-copy">
                  <p className="aq-detail-prose">
                    <strong>What happened:</strong>{" "}
                    {selectedItem.context.what_happened}
                  </p>
                  <p className="aq-detail-prose">
                    <strong>What learned:</strong>{" "}
                    {selectedItem.context.what_learned}
                  </p>
                  <p className="aq-detail-prose">
                    <strong>Action rule:</strong>{" "}
                    {selectedItem.context.action_rule}
                  </p>
                  <p className="aq-detail-prose">
                    <strong>Applies when:</strong>{" "}
                    {selectedItem.context.applies_when}
                  </p>
                  <p className="aq-detail-prose">
                    <strong>Does not apply:</strong>{" "}
                    {selectedItem.context.does_not_apply_when}
                  </p>
                </div>
              </div>

              <div className="aq-detail-section">
                <div className="aq-detail-section-label">Evidence</div>
                {selectedItem.evidence.length > 0 ? (
                  <ul className="aq-detail-list">
                    {selectedItem.evidence.map((entry) => (
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
                {selectedItem.applied_in.length > 0 ? (
                  <div className="aq-job-detail-relbuttons">
                    {selectedItem.applied_in.map((entry) => (
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
                    <span className="aq-prop-v aq-mono">{selectedItem.scope}</span>
                  </div>
                  <div className="aq-prop">
                    <span className="aq-prop-k">Tier</span>
                    <span className="aq-prop-v aq-mono">
                      {selectedItem.tier}
                    </span>
                  </div>
                  <div className="aq-prop">
                    <span className="aq-prop-k">Confidence</span>
                    <span className="aq-prop-v aq-mono">
                      {selectedItem.confidence}
                    </span>
                  </div>
                  <div className="aq-prop">
                    <span className="aq-prop-k">Status</span>
                    <span className="aq-prop-v aq-mono">
                      {selectedItem.status}
                    </span>
                  </div>
                  <div className="aq-prop">
                    <span className="aq-prop-k">Owner</span>
                    <span className="aq-prop-v aq-mono">
                      {selectedItem.owner ?? "unknown"}
                    </span>
                  </div>
                  <div className="aq-prop">
                    <span className="aq-prop-k">Review date</span>
                    <span className="aq-prop-v aq-mono">
                      {selectedItem.review_date ?? "n/a"}
                    </span>
                  </div>
                </div>
              </div>

              <div className="aq-job-detail-callout">
                <span className="aq-mono aq-mute">
                  Mutation stays outside the UI. Use `aq learning promote`,
                  `aq learning supersede`, or MCP for lifecycle changes.
                </span>
              </div>
            </aside>
          ) : null}
        </div>
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
            className={`aq-filter-pill ${
              selected === option.value ? "is-selected" : ""
            }`}
            key={option.value}
            onClick={() => onSelect(option.value)}
            type="button"
          >
            <span>{option.label}</span>
            <span className="aq-nav-count">
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
  signal: AbortSignal,
) {
  const response = await fetch(
    `/api/v1/learnings/search?query=${encodeURIComponent(query)}`,
    {
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

function ToneChip({
  label,
  tone,
}: {
  label: string;
  tone: LearningTone;
}) {
  return <span className={`aq-tone aq-tone-${tone}`}>{label}</span>;
}

function confidenceTone(confidence: string): LearningTone {
  if (confidence === "validated") {
    return "ok";
  }
  if (confidence === "confirmed") {
    return "info";
  }
  return "warn";
}

function statusTone(status: LearningStatus): LearningTone {
  if (status === "active") {
    return "ok";
  }
  if (status === "superseded") {
    return "warn";
  }
  return "mute";
}

function tierTone(tier: 1 | 2 | 3): LearningTone {
  if (tier === 3) {
    return "ok";
  }
  if (tier === 2) {
    return "info";
  }
  return "warn";
}

function formatTimestamp(value: string) {
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
  }).format(new Date(value));
}
