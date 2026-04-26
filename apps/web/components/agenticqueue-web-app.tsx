"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

import { AnalyticsView } from "@/components/analytics-view";
import { DecisionsView } from "@/components/decisions-view";
import { GraphView } from "@/components/graph-view";
import { LearningsView } from "@/components/learnings-view";
import { PipelinesView } from "@/components/pipelines-view";
import { SettingsTokensView } from "@/components/settings-tokens-view";
import { WorkView } from "@/components/work-view";
import { AQ_BUILD_VERSION } from "@/lib/build-version";

type ViewKey =
  | "pipelines"
  | "work"
  | "analytics"
  | "graph"
  | "decisions"
  | "learnings"
  | "settings"
  | "settingsTokens";

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

type SettingsContent = {
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

const SETTINGS_CONTENT: SettingsContent = {
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

  const authToken = "";

  return (
    <main className="aq-shell">
      <header className="aq-header">
        <div className="aq-shell-left">
          <Link className="aq-brand" href="/pipelines">
            <span className="aq-brand-mark">AQ</span>
            <span className="aq-brand-text">
              <span className="aq-brand-eyebrow">Coordination plane</span>
              <span className="aq-brand-title">AgenticQueue</span>
            </span>
          </Link>
          <div className="aq-area" aria-label="Current area">
            <span className="aq-area-code">AQ</span>
            <span className="aq-area-label">Public product workspace</span>
          </div>
          <PrimaryNav counts={navCounts} pathname={pathname} />
        </div>
        <div className="aq-shell-actions">
          <div className="aq-actor-chip">
            <span className="aq-actor-name">Authenticated</span>
            <span className="aq-actor-meta">browser session</span>
          </div>
          <Link className="aq-logout" href="/login">
            Log out
          </Link>
        </div>
      </header>

      <section className="aq-content">
        {view === "pipelines" ? (
          <PipelinesView authToken={authToken} />
        ) : view === "work" ? (
          <Suspense fallback={null}>
            <WorkView authToken={authToken} />
          </Suspense>
        ) : view === "analytics" ? (
          <AnalyticsView authToken={authToken} />
        ) : view === "graph" ? (
          <GraphView authToken={authToken} />
        ) : view === "decisions" ? (
          <DecisionsView authToken={authToken} />
        ) : view === "learnings" ? (
          <LearningsView authToken={authToken} />
        ) : view === "settingsTokens" ? (
          <SettingsTokensView />
        ) : view === "settings" ? (
          <>
            <div className="aq-content-head">
              <div>
                <p className="aq-content-eyebrow">{SETTINGS_CONTENT.eyebrow}</p>
                <h1 className="aq-content-title">{SETTINGS_CONTENT.title}</h1>
              </div>
              <p className="aq-content-summary">{SETTINGS_CONTENT.summary}</p>
            </div>

            <div className="aq-card-grid">
              {SETTINGS_CONTENT.cards.map((card) => (
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
              {SETTINGS_CONTENT.rows.map((row) => (
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
        ) : null}
      </section>

      <footer className="aq-footer">
        <div className="aq-footer-left aq-footer-health">
          <FooterHealthPill health={footerHealth} />
          <span className="aq-footer-pill">auth browser session</span>
          <span className="aq-footer-pill">build v{AQ_BUILD_VERSION}</span>
        </div>
        <div className="aq-footer-right">
          <Link className="aq-settings-link aq-footer-pill aq-mono" href="/settings">
            Settings
          </Link>
        </div>
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
    <nav className="aq-tabs" aria-label="Primary">
      {NAV_ITEMS.map((item) => {
        const isActive =
          (item.href === "/pipelines" && pathname === "/") ||
          pathname === item.href ||
          pathname.startsWith(`${item.href}/`);
        const count = counts?.[item.view];

        return (
          <Link
            key={item.href}
            className={isActive ? "aq-tab is-active" : "aq-tab"}
            href={item.href}
          >
            <span>{item.label}</span>
            {typeof count === "number" ? (
              <span className="aq-tab-count">{count}</span>
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
      <span className={`aq-footer-pill aq-footer-pill-${health.tone}`}>
        {health.tone === "ok" ? <span className="aq-live-dot" /> : null}
        {health.label}
      </span>
      {health.detail ? (
        <span className="aq-footer-pill">{health.detail}</span>
      ) : null}
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
