"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { FormEvent, useEffect, useState } from "react";

import { PipelinesView } from "@/components/pipelines-view";

type ViewKey =
  | "pipelines"
  | "work"
  | "graph"
  | "decisions"
  | "learnings"
  | "settings";

type AuthActor = {
  id: string;
  handle: string;
  actor_type: string;
  display_name: string;
};

type SessionPayload = {
  actor: AuthActor;
  tokenCount: number;
  apiBaseUrl: string;
};

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

const SESSION_TOKEN_KEY = "aq:web:api-token";
const PERSIST_TOKEN_KEY = "aq:web:remember-token";

const NAV_ITEMS = [
  { href: "/pipelines", label: "Pipelines", count: "12", view: "pipelines" },
  { href: "/work", label: "Work", count: "41", view: "work" },
  { href: "/graph", label: "Graph", count: "9", view: "graph" },
  { href: "/decisions", label: "Decisions", count: "18", view: "decisions" },
  { href: "/learnings", label: "Learnings", count: "47", view: "learnings" },
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
        body: "Supersede chains and ADR references stay inspectable even while agents execute elsewhere.",
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
        meta: "ADR-AQ-015",
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
        meta: "ADR-AQ-003",
      },
      {
        title: "Direct-to-main until launch",
        body: "Codex pushes clean slices directly while CI audits post-push health.",
        meta: "ADR-AQ-018",
      },
      {
        title: "Escrow over proxy",
        body: "The platform validates at check-in time rather than intercepting every tool call.",
        meta: "ADR-AQ-012",
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
        meta: "ADR-AQ-010",
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
  const [status, setStatus] = useState<"booting" | "logging-in" | "ready">(
    "booting",
  );
  const [actor, setActor] = useState<AuthActor | null>(null);
  const [authToken, setAuthToken] = useState<string | null>(null);
  const [apiBaseUrl, setApiBaseUrl] = useState("http://127.0.0.1:8010");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  useEffect(() => {
    const token = getStoredToken();

    if (!token) {
      setStatus("ready");
      setActor(null);
      setAuthToken(null);
      return;
    }

    let cancelled = false;

    setStatus("logging-in");
    setErrorMessage(null);

    void validateToken(token)
      .then((session) => {
        if (cancelled) {
          return;
        }

        setActor(session.actor);
        setAuthToken(token);
        setApiBaseUrl(session.apiBaseUrl);
        setStatus("ready");
      })
      .catch((error: unknown) => {
        if (cancelled) {
          return;
        }

        clearStoredToken();
        setActor(null);
        setAuthToken(null);
        setStatus("ready");
        setErrorMessage(errorMessageFrom(error));
      });

    return () => {
      cancelled = true;
    };
  }, []);

  async function handleLogin(token: string, remember: boolean) {
    setStatus("logging-in");
    setErrorMessage(null);

    try {
      const session = await validateToken(token);

      storeToken(token, remember);
      setActor(session.actor);
      setAuthToken(token);
      setApiBaseUrl(session.apiBaseUrl);
      setStatus("ready");
    } catch (error: unknown) {
      setActor(null);
      setAuthToken(null);
      setStatus("ready");
      setErrorMessage(errorMessageFrom(error));
    }
  }

  function handleLogout() {
    clearStoredToken();
    setActor(null);
    setAuthToken(null);
    setErrorMessage(null);
  }

  if (status !== "ready") {
    return (
      <main className="aq-auth-shell">
        <section className="aq-auth-card">
          <p className="aq-auth-kicker">Restoring session</p>
          <h1 className="aq-auth-title">Loading the AgenticQueue shell</h1>
          <p className="aq-auth-copy">
            Validating the stored API key before the UI unlocks.
          </p>
        </section>
      </main>
    );
  }

  if (!actor) {
    return (
      <LoginScreen
        errorMessage={errorMessage}
        onLogin={handleLogin}
      />
    );
  }

  const content = VIEW_CONTENT[view];

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
            <span className="aq-actor-name">{actor.display_name}</span>
            <span className="aq-actor-meta">{actor.handle}</span>
          </div>
          <button
            className="aq-logout"
            onClick={handleLogout}
            type="button"
          >
            Log out
          </button>
        </div>
      </header>

      <nav className="aq-nav" aria-label="Primary">
        {NAV_ITEMS.map((item) => {
          const isActive =
            pathname === item.href || pathname.startsWith(`${item.href}/`);

          return (
            <Link
              key={item.href}
              className={isActive ? "aq-nav-link is-active" : "aq-nav-link"}
              href={item.href}
            >
              <span>{item.label}</span>
              <span className="aq-nav-count">{item.count}</span>
            </Link>
          );
        })}
      </nav>

      <section className="aq-content">
        {view === "pipelines" && authToken ? (
          <PipelinesView authToken={authToken} />
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
          <span>api proxy ok</span>
          <span>auth actor {actor.actor_type}</span>
          <span>target {apiBaseUrl.replace(/^https?:\/\//, "")}</span>
          <span>build v0.1.0-alpha</span>
        </div>
        <Link className="aq-settings-link" href="/settings">
          Settings
        </Link>
      </footer>
    </main>
  );
}

type LoginScreenProps = {
  errorMessage: string | null;
  onLogin: (token: string, remember: boolean) => Promise<void>;
};

function LoginScreen({ errorMessage, onLogin }: LoginScreenProps) {
  const [token, setToken] = useState("");
  const [remember, setRemember] = useState(false);

  useEffect(() => {
    setRemember(window.localStorage.getItem(PERSIST_TOKEN_KEY) === "true");
  }, []);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await onLogin(token, remember);
  }

  return (
    <main className="aq-auth-shell">
      <section className="aq-auth-card">
        <p className="aq-auth-kicker">UI always on</p>
        <h1 className="aq-auth-title">Paste an AgenticQueue API key</h1>
        <p className="aq-auth-copy">
          This shell mirrors the Phase 1 actor model. No passwords, no OAuth,
          no signup flow. A valid bearer token unlocks the dashboard.
        </p>

        <form className="aq-auth-form" onSubmit={handleSubmit}>
          <label className="aq-auth-label" htmlFor="api-token">
            API token
          </label>
          <textarea
            className="aq-auth-input"
            id="api-token"
            name="api-token"
            onChange={(event) => setToken(event.target.value)}
            placeholder="aq__prefix_secret"
            rows={4}
            value={token}
          />

          <label className="aq-auth-checkbox">
            <input
              checked={remember}
              onChange={(event) => setRemember(event.target.checked)}
              type="checkbox"
            />
            <span>Remember on this device</span>
          </label>

          {errorMessage ? (
            <p className="aq-auth-error" role="alert">
              {errorMessage}
            </p>
          ) : null}

          <button className="aq-auth-submit" type="submit">
            Validate token and open shell
          </button>
        </form>

        <div className="aq-auth-notes">
          <p>Nav order is fixed: Pipelines, Work, Graph, Decisions, Learnings.</p>
          <p>Settings lives in the footer because the shell stays edge-to-edge.</p>
        </div>
      </section>
    </main>
  );
}

async function validateToken(token: string): Promise<SessionPayload> {
  const response = await fetch("/api/session", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ token }),
  });

  const payload = (await response.json().catch(() => null)) as
    | SessionPayload
    | { error?: string }
    | null;

  if (!response.ok || payload === null || !("actor" in payload)) {
    throw new Error(
      payload && "error" in payload && typeof payload.error === "string"
        ? payload.error
        : "Token validation failed.",
    );
  }

  return payload;
}

function getStoredToken() {
  const localToken = window.localStorage.getItem(SESSION_TOKEN_KEY);
  const sessionToken = window.sessionStorage.getItem(SESSION_TOKEN_KEY);

  return localToken ?? sessionToken;
}

function storeToken(token: string, remember: boolean) {
  clearStoredToken();

  if (remember) {
    window.localStorage.setItem(SESSION_TOKEN_KEY, token);
  } else {
    window.sessionStorage.setItem(SESSION_TOKEN_KEY, token);
  }

  window.localStorage.setItem(PERSIST_TOKEN_KEY, remember ? "true" : "false");
}

function clearStoredToken() {
  window.localStorage.removeItem(SESSION_TOKEN_KEY);
  window.sessionStorage.removeItem(SESSION_TOKEN_KEY);
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

function errorMessageFrom(error: unknown) {
  return error instanceof Error ? error.message : "Unexpected auth failure.";
}
