# Analytics Benchmark Evidence

Source: `scripts/benchmark_analytics.py`

This note captures the AQ-196 follow-up benchmark for the Phase 7 analytics
dashboard.

Command:

```powershell
D:\mmmmm\agenticqueue\.venv\Scripts\python.exe scripts/benchmark_analytics.py --output-json docs/benchmarks/analytics-dashboard-10k.json
```

The benchmark seeds a rollback-only dataset, measures the live analytics query
paths plus `GET /v1/analytics/metrics`, checks the relevant EXPLAIN plans for
large-table sequential scans, and then rolls the dataset back.

## Dataset

- Captured at: `2026-04-21T17:01:11.770289+00:00`
- Rolling window: `90d`
- Recent workload rows inside the measured window: `10,000`
- Cold-history workload rows outside the window: `90,000`
- Blocker tasks: `120`
- Total seeded tasks: `100,120`
- Total seeded runs: `100,000`
- Blocked tasks in-window: `1,000`
- Packet-version samples: `400`

The cold-history rows are intentional. They make the rolling-window plans behave
like a long-lived production queue instead of a brand-new database where every
row is still in the active slice.

## Warm Latency

25 measured runs after 5 warmups:

| Path | p50 ms | p95 ms | max ms |
|---|---:|---:|---:|
| `cycle_time` | 3.78 | 6.98 | 9.62 |
| `blocked_heatmap` | 22.78 | 32.17 | 139.25 |
| `handoff_metrics` | 40.51 | 65.92 | 93.65 |
| `GET /v1/analytics/metrics` | 96.14 | 159.09 | 241.31 |

Result:
- All core query paths stayed under the `100 ms` warm p95 budget.
- The full analytics endpoint stayed under the `250 ms` warm p95 budget and
  well inside the dashboard's `<2 s` load target.

## EXPLAIN Summary

| Path | Root node | Actual total ms | Seq scans seen | Notes |
|---|---|---:|---|---|
| `cycle_time` | `Sort` | 4.273 | none | No seq scan on large tables. |
| `blocked_task_lookup` | `Sort` | 0.493 | none | No seq scan on large tables. |
| `blocked_edge_lookup` | `Sort` | 0.338 | `edge` | Acceptable in this benchmark because only `1,000` edge rows are seeded; the harness only fails seq scans on tables at or above `10,000` rows. |
| `handoff_run_scan` | `Hash Join` | 4.453 | `actor` | Acceptable because `actor` is a tiny dimension table; `run` itself avoids a seq scan. |

## Artifacts

- Human-readable note: `docs/benchmarks/analytics-dashboard-10k.md`
- Raw JSON capture: `docs/benchmarks/analytics-dashboard-10k.json`

## Follow-up Outcome

AQ-196's benchmark closed the remaining AQ-110 evidence gap:
- representative rolling-window timing recorded
- large-table EXPLAIN evidence recorded
- regression harness committed so the benchmark can be re-run on demand
