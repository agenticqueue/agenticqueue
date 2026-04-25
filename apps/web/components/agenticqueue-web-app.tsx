"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

import { AnalyticsView } from "@/components/analytics-view";
import { DecisionsView } from "@/components/decisions-view";
import { GraphView } from "@/components/graph-view";
import { LearningsView } from "@/components/learnings-view";
import { PipelinesView } from "@/components/pipelines-view";
import { WorkView } from "@/components/work-view";
import { AQ_BUILD_VERSION } from "@/lib/build-version";

type ViewKey =
  | "pipelines"
  | "work"
  | "analytics"
  | "graph"
  | "decisions"
  | "learnings"
  | "settings";

type FooterHealthTone = "ok" | "warn" | "danger";

type FooterHealthLabel = "ok" | "degraded" | "unreachable";

type FooterHealthState = {
  label: FooterHealthLabel;
  tone: FooterHealthTone;
  detail: string | null;
};

type HealthResponsePayload = {
  status?: string;
  deps?: {
    api?: {
      status?: string;
      http_status?: number;
    };
  };
};

type NavView = Exclude<ViewKey, "settings">;

export type NavCounts = Partial<Record<NavView, number>>;

type ViewDefinition = {
  title: string;
  eyebrow: string;
  summary: string;
  cards: Array<{
    label: string;
    value: string;
    tone: "info" | "ok" | "warn";
  }>;
  rows: Array<{
    title: string;
    body: string;
    meta: string;
  }>;
};

type AgenticQueueWebAppProps = {
  view: ViewKey;
};

const HEALTH_POLL_INTERVAL_MS = 30_000;
const DEFAULT_FOOTER_HEALTH: FooterHealthState = {
  label: "degraded",
  tone: "warn",
  detail: "api: checking",
};

const NAV_ITEMS = [
  { href: "/pipelines", label: "Pipelines", view: "pipelines" },
  { href: "/work", label: "Work", view: "work" },
  { href: "/analytics", label: "Analytics", view: "analytics" },
  { href: "/graph", label: "Graph", view: "graph" },
  { href: "/decisions", label: "Decisions", view: "decisions" },
  { href: "/learnings", label: "Learnings", view: "learnings" },
] as const;

const VIEW_CONTENT: Record<ViewKey, ViewDefinition> = {
  pipelines: {
    title: "Pipelines",
    eyebrow: "Phase 7 shell",
    summary:
      "Human-first overview of the coordination plane with pipeline status, ownership, and queue pressure visible at a glance.",
    cards: [
      { label: "Live pipelines", value: "12", tone: "info" },
      { label: "Blocked jobs", value: "4", tone: "warn" },
      { label: "Auto-approved today", value: "28", tone: "ok" },
    ],
    rows: [
      {
        title: "Foundation rollout",
        body: "Core contracts, learnings, and packet compiler are green. Phase 7 now owns the observability shell.",
        meta: "api packet v17 · 2 blockers cleared",
      },
      {
        title: "Customer sandbox readiness",
        body: "Single-node compose path is stable; first-run setup and docs are the next release boundary.",
        meta: "deployment track · reviewer-free",
      },
      {
        title: "Public launch prep",
        body: "Website, docs, and release hardening stay visible here even when HITL is disabled.",
        meta: "phase 13 · launch surface",
      },
    ],
  },
  work: {
    title: "Work",
    eyebrow: "Cross-pipeline queue",
    summary:
      "A single queue lens for claimed, blocked, and ready jobs without leaving the always-on shell.",
    cards: [
      { label: "Claimable", value: "9", tone: "ok" },
      { label: "Needs review", value: "3", tone: "warn" },
      { label: "Mean cycle", value: "18m", tone: "info" },
    ],
    rows: [
      {
        title: "AQ-102 Next.js scaffold",
        body: "Token-gated shell, route-aware nav, and design-token port from the private preview.",
        meta: "agent:codex · in progress",
      },
      {
        title: "AQ-103 Pipelines view",
        body: "Filter-as-sentence UX and density toggle stay queued behind the scaffold.",
        meta: "phase 7 · urgent follow-on",
      },
      {
        title: "AQ-115 Playwright smoke",
        body: "Critical-path coverage for the five primary views once the shell is stable.",
        meta: "quality gate · queued",
      },
    ],
  },
  analytics: {
    title: "Analytics",
    eyebrow: "Operator telemetry",
    summary:
      "Cycle time, blocked work, handoff latency, retrieval precision, success rates, and review load stay visible from the same always-on shell.",
    cards: [
      { label: "Tracked windows", value: "3", tone: "info" },
      { label: "Core panels", value: "6", tone: "ok" },
      { label: "Write paths", value: "0", tone: "warn" },
    ],
    rows: [
      {
        title: "Read-only by design",
        body: "Analytics is an observability surface over existing API read models, not a backdoor mutation channel.",
        meta: "read-only policy",
      },
      {
        title: "90-day window first",
        body: "The default query shape is tuned for the operator view AQ-110 asked for, with room to widen when needed.",
        meta: "aq-110",
      },
      {
        title: "Precision is grounded in packet evidence",
        body: "Retrieval quality comes from packet payload overlap, not synthetic scoring or dashboard-only fixtures.",
        meta: "no synthetic data",
      },
    ],
  },
  graph: {
    title: "Graph",
    eyebrow: "Execution + decision topology",
    summary:
      "Visual graph space is reserved for decision lineage, execution paths, and dependency chains without toggling the UI off.",
    cards: [
      { label: "Decision nodes", value: "58", tone: "info" },
      { label: "Surface tags", value: "143", tone: "ok" },
      { label: "Stale edges", value: "0", tone: "ok" },
    ],
    rows: [
      {
        title: "Decision lineage",
        body: "Supersede chains and decision references stay inspectable even while agents execute elsewhere.",
        meta: "graph-first retrieval",
      },
      {
        title: "Execution topology",
        body: "Runs, jobs, and artifacts converge into one graph instead of scattering across tools.",
        meta: "escrow model · no middleman",
      },
      {
        title: "Surface-area links",
        body: "Learnings, tasks, and artifacts stay joined through deterministic tags before any fuzzy retrieval.",
        meta: "surface-area retrieval",
      },
    ],
  },
  decisions: {
    title: "Decisions",
    eyebrow: "Governance ledger",
    summary:
      "Human-readable decision records sit beside task flow so governance remains visible from the shell instead of buried in docs.",
    cards: [
      { label: "Accepted", value: "24", tone: "ok" },
      { label: "Superseded", value: "3", tone: "warn" },
      { label: "Queued drafts", value: "2", tone: "info" },
    ],
    rows: [
      {
        title: "UI stays on in every mode",
        body: "Observability is not optional, even when policy packs disable human approval on the happy path.",
        meta: "observability policy",
      },
      {
        title: "Direct-to-main until launch",
        body: "Codex pushes clean slices directly while CI audits post-push health.",
        meta: "release workflow",
      },
      {
        title: "Escrow over proxy",
        body: "The platform validates at check-in time rather than intercepting every tool call.",
        meta: "contract validation",
      },
    ],
  },
  learnings: {
    title: "Learnings",
    eyebrow: "First-class anti-repeat system",
    summary:
      "Learnings stay top-level in the nav because the system is supposed to remember what prior runs already proved.",
    cards: [
      { label: "Task-scope", value: "29", tone: "info" },
      { label: "Project-scope", value: "14", tone: "ok" },
      { label: "Validated", value: "11", tone: "ok" },
    ],
    rows: [
      {
        title: "Surface-area tags drive relevance",
        body: "Relevant learnings are pulled by deterministic overlap before any optional fuzzy retrieval.",
        meta: "retrieval tier 2",
      },
      {
        title: "Failures should draft memory",
        body: "Blocked, retried, or corrected work should emit structured learnings before closeout.",
        meta: "anti-repeat loop",
      },
      {
        title: "Human review upgrades confidence",
        body: "Validated learnings move from one task into project memory when the evidence repeats.",
        meta: "promotion pipeline",
      },
    ],
  },
  settings: {
    title: "Settings",
    eyebrow: "Workspace + session controls",
    summary:
      "Bearer-token auth stays intentionally simple: paste a key, validate it, and clear it to log out.",
    cards: [
      { label: "Auth mode", value: "Bearer only", tone: "info" },
      { label: "Storage", value: "Session / local", tone: "ok" },
      { label: "UI mode", value: "Always on", tone: "ok" },
    ],
    rows: [
      {
        title: "Session storage is the default",
        body: "Keys stay in session storage unless the user explicitly opts into local persistence on the login screen.",
        meta: "AQ-102 scope",
      },
      {
        title: "No passwords or OAuth",
        body: "The shell mirrors the API actor model instead of inventing a parallel identity system.",
        meta: "token auth only",
      },
      {
        title: "Future work stays additive",
        body: "RBAC, OIDC, and SCIM can layer on later without rewriting the phase-7 shell.",
        meta: "phase 9 follow-ons",
      },
    ],
  },
};

export function AgenticQueueWebApp({ view }: AgenticQueueWebAppProps) {
  const pathname = usePathname();
  const [navCounts, setNavCounts] = useState<NavCounts | null>(null);
  const [footerHealth, setFooterHealth] =
    useState<FooterHealthState>(DEFAULT_FOOTER_HEALTH);

  useEffect(() => {
    let cancelled = false;
    let activeController: AbortController | null = null;

    async function loadNavCounts() {
      activeController?.abort();
      const controller = new AbortController();
      activeController = controller;

      try {
        const response = await fetch("/api/v1/nav-counts", {
          cache: "no-store",
          signal: controller.signal,
        });

        const payload = (await response.json().catch(() => null)) as
          | NavCounts
          | { error?: string }
          | null;

        if (!response.ok || !isNavCountsPayload(payload)) {
          throw new Error(
            payload && "error" in payload && typeof payload.error === "string"
              ? payload.error
              : "Nav counts request failed.",
          );
        }

        if (cancelled || controller.signal.aborted) {
          return;
        }

        setNavCounts(payload);
      } catch {
        if (cancelled || controller.signal.aborted) {
          return;
        }

        setNavCounts(null);
      }
    }

    void loadNavCounts();

    const handleFocus = () => {
      void loadNavCounts();
    };

    window.addEventListener("focus", handleFocus);

    return () => {
      cancelled = true;
      activeController?.abort();
      window.removeEventListener("focus", handleFocus);
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    let activeController: AbortController | null = null;

    async function loadFooterHealth() {
      activeController?.abort();
      const controller = new AbortController();
      activeController = controller;

      try {
        const response = await fetch("/api/health", {
          cache: "no-store",
          signal: controller.signal,
        });
        const payload = (await response.json().catch(() => null)) as
          | HealthResponsePayload
          | null;

        if (cancelled || controller.signal.aborted) {
          return;
        }

        setFooterHealth(resolveFooterHealth(payload));
      } catch {
        if (cancelled || activeController?.signal.aborted) {
          return;
        }

        setFooterHealth({
          label: "unreachable",
          tone: "danger",
          detail: "api: unreachable",
        });
      }
    }

    void loadFooterHealth();
    const interval = window.setInterval(() => {
      void loadFooterHealth();
    }, HEALTH_POLL_INTERVAL_MS);

    return () => {
      cancelled = true;
      activeController?.abort();
      window.clearInterval(interval);
    };
  }, []);

  const content = VIEW_CONTENT[view];
  const authToken = "";

  return (
    <main className="aq-shell">
      <header className="aq-topbar">
        <div className="aq-topbar-left">
          <div className="aq-brand-lockup">
            <span className="aq-brand-mark">AQ</span>
            <div>
              <p className="aq-brand-eyebrow">Coordination plane</p>
              <p className="aq-brand-title">AgenticQueue</p>
            </div>
          </div>
          <div className="aq-workspace-pill">
            <span className="aq-workspace-code">AQ</span>
            <span>Public product workspace</span>
          </div>
        </div>
        <div className="aq-topbar-right">
          <div className="aq-actor-chip">
            <span className="aq-actor-name">Authenticated</span>
            <span className="aq-actor-meta">browser session</span>
          </div>
          <Link className="aq-logout" href="/login">
            Log out
          </Link>
        </div>
      </header>

      <PrimaryNav counts={navCounts} pathname={pathname} />

      <section className="aq-content">
        {view === "pipelines" ? (
          <PipelinesView authToken={authToken} />
        ) : view === "work" ? (
          <WorkView authToken={authToken} />
        ) : view === "analytics" ? (
          <AnalyticsView authToken={authToken} />
        ) : view === "graph" ? (
          <GraphView authToken={authToken} />
        ) : view === "decisions" ? (
          <DecisionsView authToken={authToken} />
        ) : view === "learnings" ? (
          <LearningsView authToken={authToken} />
        ) : (
          <>
            <div className="aq-content-head">
              <div>
                <p className="aq-content-eyebrow">{content.eyebrow}</p>
                <h1 className="aq-content-title">{content.title}</h1>
              </div>
              <p className="aq-content-summary">{content.summary}</p>
            </div>

            <div className="aq-card-grid">
              {content.cards.map((card) => (
                <article key={card.label} className="aq-stat-card">
                  <p className="aq-stat-label">{card.label}</p>
                  <div className="aq-stat-line">
                    <strong className="aq-stat-value">{card.value}</strong>
                    <span className={`aq-tone aq-tone-${card.tone}`}>
                      {toneLabel(card.tone)}
                    </span>
                  </div>
                </article>
              ))}
            </div>

            <div className="aq-surface-list">
              {content.rows.map((row) => (
                <article key={row.title} className="aq-surface-row">
                  <div>
                    <h2 className="aq-row-title">{row.title}</h2>
                    <p className="aq-row-body">{row.body}</p>
                  </div>
                  <p className="aq-row-meta">{row.meta}</p>
                </article>
              ))}
            </div>
          </>
        )}
      </section>

      <footer className="aq-footer">
        <div className="aq-footer-health">
          <FooterHealthPill health={footerHealth} />
          <span>auth browser session</span>
          <span>build v{AQ_BUILD_VERSION}</span>
        </div>
        <Link className="aq-settings-link" href="/settings">
          Settings
        </Link>
      </footer>
    </main>
  );
}

export function PrimaryNav({
  counts,
  pathname,
}: {
  counts: NavCounts | null;
  pathname: string;
}) {
  return (
    <nav className="aq-nav" aria-label="Primary">
      {NAV_ITEMS.map((item) => {
        const isActive =
          (item.href === "/pipelines" && pathname === "/") ||
          pathname === item.href ||
          pathname.startsWith(`${item.href}/`);
        const count = counts?.[item.view];

        return (
          <Link
            key={item.href}
            className={isActive ? "aq-nav-link is-active" : "aq-nav-link"}
            href={item.href}
          >
            <span>{item.label}</span>
            {typeof count === "number" ? (
              <span className="aq-nav-count">{count}</span>
            ) : null}
          </Link>
        );
      })}
    </nav>
  );
}

export function FooterHealthPill({
  health,
}: {
  health: FooterHealthState;
}) {
  return (
    <>
      <span className={`aq-tone aq-tone-${health.tone}`}>{health.label}</span>
      {health.detail ? <span>{health.detail}</span> : null}
    </>
  );
}

function toneLabel(tone: "info" | "ok" | "warn") {
  if (tone === "ok") {
    return "stable";
  }

  if (tone === "warn") {
    return "watch";
  }

  return "live";
}

function isNavCountsPayload(payload: unknown): payload is Required<NavCounts> {
  if (!payload || typeof payload !== "object") {
    return false;
  }

  const record = payload as Record<string, unknown>;
  return NAV_ITEMS.every((item) => typeof record[item.view] === "number");
}

function resolveFooterHealth(
  payload: HealthResponsePayload | null,
): FooterHealthState {
  const apiDependency = payload?.deps?.api;

  if (apiDependency?.status === "unreachable") {
    return {
      label: "unreachable",
      tone: "danger",
      detail: "api: unreachable",
    };
  }

  if (apiDependency?.status === "error") {
    return {
      label: "degraded",
      tone: "warn",
      detail:
        typeof apiDependency.http_status === "number"
          ? `api: error ${apiDependency.http_status}`
          : "api: error",
    };
  }

  if (payload?.status === "ok") {
    return {
      label: "ok",
      tone: "ok",
      detail: null,
    };
  }

  if (payload?.status === "degraded" || apiDependency?.status === "degraded") {
    return {
      label: "degraded",
      tone: "warn",
      detail: "api: degraded",
    };
  }

  return DEFAULT_FOOTER_HEALTH;
}
