"use client";

import { useEffect, useMemo, useState } from "react";

type WindowKey = "30d" | "90d" | "180d";

type CycleTimeMetric = {
  task_type: string;
  count: number;
  median_hours: number;
  p95_hours: number;
};

type BlockedHeatmapCell = {
  blocker_ref: string;
  blocker_title: string;
  task_count: number;
  total_blocked_hours: number;
  p95_blocked_hours: number;
  sample_refs: string[];
};

type HistogramBucket = {
  label: string;
  min_minutes: number;
  max_minutes: number | null;
  count: number;
};

type ActorLatency = {
  actor: string;
  count: number;
  median_minutes: number;
  p95_minutes: number;
};

type RetrievalPrecisionMetric = {
  sample_size: number;
  precision_at_5: number;
  precision_at_10: number;
  note: string;
};

type AgentSuccessMetric = {
  actor: string;
  complete_count: number;
  parked_count: number;
  error_count: number;
  total_count: number;
  success_rate: number;
};

type ReviewLoadPoint = {
  day: string;
  count: number;
};

type AnalyticsResponse = {
  generated_at: string;
  window: {
    key: string;
    days: number;
    start_at: string;
    end_at: string;
  };
  cycle_time: CycleTimeMetric[];
  blocked_heatmap: BlockedHeatmapCell[];
  handoff_latency_histogram: HistogramBucket[];
  handoff_latency_by_actor: ActorLatency[];
  retrieval_precision: RetrievalPrecisionMetric;
  agent_success_rates: AgentSuccessMetric[];
  review_load: ReviewLoadPoint[];
};

type AnalyticsViewProps = {
  authToken: string;
};

const WINDOW_OPTIONS: Array<{ value: WindowKey; label: string }> = [
  { value: "30d", label: "Last 30d" },
  { value: "90d", label: "Last 90d" },
  { value: "180d", label: "Last 180d" },
];

export function AnalyticsView({ authToken }: AnalyticsViewProps) {
  const [windowKey, setWindowKey] = useState<WindowKey>("90d");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [data, setData] = useState<AnalyticsResponse | null>(null);
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
        setLoading((current) => current && data === null);
        setError(null);

        const response = await fetch(`/api/v1/analytics/metrics?window=${windowKey}`, {
          headers: {
            Authorization: `Bearer ${authToken}`,
          },
          cache: "no-store",
          signal: controller.signal,
        });

        const payload = (await response.json().catch(() => null)) as
          | AnalyticsResponse
          | { error?: string }
          | null;

        if (!response.ok || payload === null || !("cycle_time" in payload)) {
          throw new Error(
            payload && "error" in payload && typeof payload.error === "string"
              ? payload.error
              : "Analytics request failed.",
          );
        }

        if (cancelled) {
          return;
        }

        setData(payload);
      } catch (requestError: unknown) {
        if (cancelled || controller.signal.aborted) {
          return;
        }

        setError(
          requestError instanceof Error
            ? requestError.message
            : "Failed to load analytics view.",
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
  }, [authToken, data, refreshNonce, windowKey]);

  const cycleTime = data?.cycle_time ?? [];
  const blockedHeatmap = data?.blocked_heatmap ?? [];
  const handoffHistogram = data?.handoff_latency_histogram ?? [];
  const handoffByActor = data?.handoff_latency_by_actor ?? [];
  const agentSuccessRates = data?.agent_success_rates ?? [];
  const reviewLoad = data?.review_load;
  const retrievalPrecision = data?.retrieval_precision ?? {
    sample_size: 0,
    precision_at_5: 0,
    precision_at_10: 0,
    note: "No retrieval samples yet.",
  };

  const topCycleMetric = cycleTime[0] ?? null;
  const totalHistogramSamples = handoffHistogram.reduce(
    (total, bucket) => total + bucket.count,
    0,
  );
  const peakReviewLoad = (reviewLoad ?? []).reduce(
    (maxCount, point) => Math.max(maxCount, point.count),
    0,
  );
  const totalBlockedHours = blockedHeatmap.reduce(
    (total, bucket) => total + bucket.total_blocked_hours,
    0,
  );
  const recentReviewLoad = useMemo(
    () => (reviewLoad ?? []).slice(-21),
    [reviewLoad],
  );
  const maxBlockedHours = blockedHeatmap.reduce(
    (maxCount, bucket) => Math.max(maxCount, bucket.total_blocked_hours),
    0,
  );
  const maxHistogramCount = handoffHistogram.reduce(
    (maxCount, bucket) => Math.max(maxCount, bucket.count),
    0,
  );
  const maxReviewCount = recentReviewLoad.reduce(
    (maxCount, point) => Math.max(maxCount, point.count),
    0,
  );

  return (
    <div className="aq-analytics-view">
      <div className="aq-content-head aq-content-head-pipelines">
        <div>
          <p className="aq-content-eyebrow">Phase 7 live view</p>
          <h1 className="aq-content-title">Analytics</h1>
        </div>
        <p className="aq-content-summary">
          Read-only operator telemetry for cycle time, blocked work, handoff
          latency, retrieval quality, agent success, and review pressure.
        </p>
      </div>

      <div className="aq-pipelines-readonly">
        <span className="aq-pipelines-readonly-kicker">read-only</span>
        <span>
          Metrics are aggregated from live task, run, edge, and packet data.
          This dashboard observes the system; it does not mutate it.
        </span>
      </div>

      <div className="aq-work-toolbar">
        <div className="aq-work-toolbar-meta">
          <span className="aq-mono aq-mute">
            window {data?.window.key ?? windowKey} · {data?.window.days ?? 0} days
          </span>
          {data?.generated_at ? (
            <span className="aq-mono aq-mute">
              last sync {formatTimestamp(data.generated_at)}
            </span>
          ) : null}
          <span className="aq-mono aq-mute">
            retrieval samples {retrievalPrecision.sample_size}
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

      <div className="aq-filter-group">
        <span className="aq-filter-label">Window</span>
        <div className="aq-filter-pills">
          {WINDOW_OPTIONS.map((option) => (
            <button
              className={`aq-filter-pill ${windowKey === option.value ? "is-selected" : ""}`}
              key={option.value}
              onClick={() => setWindowKey(option.value)}
              type="button"
            >
              <span>{option.label}</span>
              <span className="aq-nav-count">{option.value}</span>
            </button>
          ))}
        </div>
      </div>

      {loading ? (
        <div className="aq-pipelines-state">
          <p className="aq-auth-kicker">Loading analytics</p>
          <h2 className="aq-state-title">Building the read-only metrics view</h2>
          <p className="aq-state-copy">
            Aggregating tasks, runs, packet retrieval, and review pressure into
            a single operator dashboard.
          </p>
        </div>
      ) : error ? (
        <div className="aq-pipelines-state is-error" role="alert">
          <p className="aq-auth-kicker">Load failure</p>
          <h2 className="aq-state-title">Analytics could not be loaded</h2>
          <p className="aq-state-copy">{error}</p>
        </div>
      ) : data ? (
        <>
          <div className="aq-analytics-hero">
            <article className="aq-analytics-kpi">
              <p className="aq-stat-label">Top cycle median</p>
              <strong className="aq-stat-value">
                {topCycleMetric ? formatHours(topCycleMetric.median_hours) : "--"}
              </strong>
              <p className="aq-row-body">
                {topCycleMetric
                  ? `${topCycleMetric.task_type} · p95 ${formatHours(topCycleMetric.p95_hours)}`
                  : "No completed work in this window."}
              </p>
            </article>
            <article className="aq-analytics-kpi">
              <p className="aq-stat-label">Blocked hours</p>
              <strong className="aq-stat-value">{formatHours(totalBlockedHours)}</strong>
              <p className="aq-row-body">
                {blockedHeatmap.length} blocker lanes across the current queue.
              </p>
            </article>
            <article className="aq-analytics-kpi">
              <p className="aq-stat-label">Precision at 5</p>
              <strong className="aq-stat-value">
                {formatPercent(retrievalPrecision.precision_at_5)}
              </strong>
              <p className="aq-row-body">
                Precision at 10 {formatPercent(retrievalPrecision.precision_at_10)}.
              </p>
            </article>
            <article className="aq-analytics-kpi">
              <p className="aq-stat-label">Peak review load</p>
              <strong className="aq-stat-value">{peakReviewLoad}</strong>
              <p className="aq-row-body">
                Visible daily queue pressure inside the selected window.
              </p>
            </article>
          </div>

          <div className="aq-analytics-grid">
            <section className="aq-analytics-panel" data-testid="analytics-cycle-time">
              <div className="aq-analytics-panel-head">
                <div>
                  <p className="aq-detail-section-label">Cycle time</p>
                  <h2 className="aq-row-title">Median and p95 by task type</h2>
                </div>
                <ToneChip label={`${cycleTime.length} tracked`} tone="info" />
              </div>
              {cycleTime.length > 0 ? (
                <div className="aq-analytics-list">
                  {cycleTime.map((metric) => (
                    <article className="aq-inline-card" key={metric.task_type}>
                      <div className="aq-analytics-row-head">
                        <span>{metric.task_type}</span>
                        <span className="aq-mono aq-mute">{metric.count} tasks</span>
                      </div>
                      <div className="aq-analytics-statpair">
                        <div className="aq-analytics-stat">
                          <span className="aq-prop-k">Median</span>
                          <strong>{formatHours(metric.median_hours)}</strong>
                        </div>
                        <div className="aq-analytics-stat">
                          <span className="aq-prop-k">P95</span>
                          <strong>{formatHours(metric.p95_hours)}</strong>
                        </div>
                      </div>
                    </article>
                  ))}
                </div>
              ) : (
                <p className="aq-detail-prose aq-mute">
                  No completed task types were recorded in this window.
                </p>
              )}
            </section>

            <section className="aq-analytics-panel" data-testid="analytics-blocked">
              <div className="aq-analytics-panel-head">
                <div>
                  <p className="aq-detail-section-label">Blocked work</p>
                  <h2 className="aq-row-title">Current blocker heatmap</h2>
                </div>
                <ToneChip label={`${blockedHeatmap.length} blockers`} tone="warn" />
              </div>
              {blockedHeatmap.length > 0 ? (
                <div className="aq-analytics-list">
                  {blockedHeatmap.map((bucket) => (
                    <article className="aq-output-row" key={bucket.blocker_ref}>
                      <div className="aq-analytics-row-copy">
                        <div className="aq-analytics-row-head">
                          <span>{bucket.blocker_title}</span>
                          <span className="aq-mono aq-mute">{bucket.blocker_ref}</span>
                        </div>
                        <div className="aq-analytics-bar-track" aria-hidden="true">
                          <div
                            className="aq-analytics-bar-fill aq-analytics-bar-fill-warn"
                            style={{
                              width: percentWidth(
                                bucket.total_blocked_hours,
                                maxBlockedHours,
                              ),
                            }}
                          />
                        </div>
                        <p className="aq-row-body">
                          {bucket.task_count} blocked jobs · samples{" "}
                          {bucket.sample_refs.join(", ")}
                        </p>
                      </div>
                      <div className="aq-output-meta">
                        <span className="aq-mono aq-mute">
                          total {formatHours(bucket.total_blocked_hours)}
                        </span>
                        <span className="aq-mono aq-mute">
                          p95 {formatHours(bucket.p95_blocked_hours)}
                        </span>
                      </div>
                    </article>
                  ))}
                </div>
              ) : (
                <p className="aq-detail-prose aq-mute">
                  No blocked or parked jobs are active in this window.
                </p>
              )}
            </section>

            <section className="aq-analytics-panel" data-testid="analytics-handoff">
              <div className="aq-analytics-panel-head">
                <div>
                  <p className="aq-detail-section-label">Handoff latency</p>
                  <h2 className="aq-row-title">Histogram + actor latency</h2>
                </div>
                <ToneChip label={`${totalHistogramSamples} runs`} tone="info" />
              </div>
              {handoffHistogram.length > 0 ? (
                <div className="aq-analytics-list">
                  {handoffHistogram.map((bucket) => (
                    <article className="aq-inline-card" key={bucket.label}>
                      <div className="aq-analytics-row-head">
                        <span>{bucket.label}</span>
                        <span className="aq-mono aq-mute">{bucket.count} runs</span>
                      </div>
                      <div className="aq-analytics-bar-track" aria-hidden="true">
                        <div
                          className="aq-analytics-bar-fill"
                          style={{
                            width: percentWidth(bucket.count, maxHistogramCount),
                          }}
                        />
                      </div>
                    </article>
                  ))}
                </div>
              ) : (
                <p className="aq-detail-prose aq-mute">
                  No completed runs were recorded in this window.
                </p>
              )}
              {handoffByActor.length > 0 ? (
                <div className="aq-analytics-actor-strip">
                  {handoffByActor.slice(0, 4).map((metric) => (
                    <article className="aq-prop" key={metric.actor}>
                      <span className="aq-prop-k">{metric.actor}</span>
                      <span className="aq-prop-v">
                        {formatMinutes(metric.median_minutes)} median
                      </span>
                      <span className="aq-mono aq-mute">
                        p95 {formatMinutes(metric.p95_minutes)} · {metric.count} runs
                      </span>
                    </article>
                  ))}
                </div>
              ) : null}
            </section>

            <section
              className="aq-analytics-panel"
              data-testid="analytics-retrieval"
            >
              <div className="aq-analytics-panel-head">
                <div>
                  <p className="aq-detail-section-label">Retrieval precision</p>
                  <h2 className="aq-row-title">Packet-backed relevance quality</h2>
                </div>
                <ToneChip
                  label={`${retrievalPrecision.sample_size} packets`}
                  tone={retrievalPrecision.sample_size > 0 ? "ok" : "mute"}
                />
              </div>
              <div className="aq-analytics-statpair">
                <div className="aq-analytics-stat">
                  <span className="aq-prop-k">Precision@5</span>
                  <strong>{formatPercent(retrievalPrecision.precision_at_5)}</strong>
                </div>
                <div className="aq-analytics-stat">
                  <span className="aq-prop-k">Precision@10</span>
                  <strong>{formatPercent(retrievalPrecision.precision_at_10)}</strong>
                </div>
              </div>
              <p className="aq-detail-prose">{retrievalPrecision.note}</p>
            </section>

            <section
              className="aq-analytics-panel"
              data-testid="analytics-agent-success"
            >
              <div className="aq-analytics-panel-head">
                <div>
                  <p className="aq-detail-section-label">Agent success</p>
                  <h2 className="aq-row-title">Outcome mix by actor</h2>
                </div>
                <ToneChip label={`${agentSuccessRates.length} actors`} tone="ok" />
              </div>
              {agentSuccessRates.length > 0 ? (
                <div className="aq-analytics-list">
                  {agentSuccessRates.map((metric) => (
                    <article className="aq-inline-card" key={metric.actor}>
                      <div className="aq-analytics-row-head">
                        <span>{metric.actor}</span>
                        <span className="aq-mono aq-mute">
                          {metric.total_count} runs
                        </span>
                      </div>
                      <div className="aq-analytics-bar-track" aria-hidden="true">
                        <div
                          className="aq-analytics-bar-fill aq-analytics-bar-fill-ok"
                          style={{
                            width: percentWidth(metric.success_rate, 1),
                          }}
                        />
                      </div>
                      <div className="aq-analytics-row-meta">
                        <span className="aq-mono aq-mute">
                          success {formatPercent(metric.success_rate)}
                        </span>
                        <span className="aq-mono aq-mute">
                          {metric.complete_count} complete / {metric.parked_count} parked /{" "}
                          {metric.error_count} error
                        </span>
                      </div>
                    </article>
                  ))}
                </div>
              ) : (
                <p className="aq-detail-prose aq-mute">
                  No completed run outcomes were recorded in this window.
                </p>
              )}
            </section>

            <section className="aq-analytics-panel" data-testid="analytics-review-load">
              <div className="aq-analytics-panel-head">
                <div>
                  <p className="aq-detail-section-label">Review load</p>
                  <h2 className="aq-row-title">Daily review queue pressure</h2>
                </div>
                <ToneChip label={`peak ${peakReviewLoad}`} tone="warn" />
              </div>
              {recentReviewLoad.length > 0 ? (
                <>
                  <div className="aq-analytics-sparkline" aria-hidden="true">
                    {recentReviewLoad.map((point) => (
                      <div
                        className="aq-analytics-sparkbar"
                        key={point.day}
                        style={{
                          height: percentWidth(point.count, maxReviewCount || 1),
                        }}
                        title={`${point.day}: ${point.count}`}
                      />
                    ))}
                  </div>
                  <div className="aq-analytics-row-meta">
                    <span className="aq-mono aq-mute">
                      last {recentReviewLoad.length} days shown
                    </span>
                    <span className="aq-mono aq-mute">
                      {reviewLoad?.length ?? 0} daily points in window
                    </span>
                  </div>
                </>
              ) : (
                <p className="aq-detail-prose aq-mute">
                  No review queue signals were recorded in this window.
                </p>
              )}
            </section>
          </div>
        </>
      ) : null}
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

function formatTimestamp(value: string) {
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(value));
}

function formatHours(value: number) {
  return `${value.toFixed(1)}h`;
}

function formatMinutes(value: number) {
  return `${Math.round(value)}m`;
}

function formatPercent(value: number) {
  return `${Math.round(value * 100)}%`;
}

function percentWidth(value: number, maxValue: number) {
  if (value <= 0 || maxValue <= 0) {
    return "0%";
  }

  return `${Math.max((value / maxValue) * 100, 8)}%`;
}
