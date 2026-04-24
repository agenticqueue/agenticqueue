"use client";

import { useEffect, useState } from "react";

type ApiToken = {
  id: string;
  token_prefix: string;
  scopes: string[];
  created_at: string;
  updated_at: string;
  expires_at?: string | null;
  revoked_at?: string | null;
};

type TokenListResponse = {
  tokens: ApiToken[];
};

type TokenCreateResponse = {
  token: string;
  api_token: ApiToken;
};

export function TokenSettingsView() {
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [tokens, setTokens] = useState<ApiToken[]>([]);
  const [newToken, setNewToken] = useState<string | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();

    async function loadTokens() {
      try {
        setLoading(true);
        setErrorMessage(null);
        const payload = await fetchTokens(controller.signal);
        setTokens(payload.tokens);
      } catch (error: unknown) {
        if (!controller.signal.aborted) {
          setErrorMessage(errorMessageFrom(error, "API tokens could not be loaded."));
        }
      } finally {
        if (!controller.signal.aborted) {
          setLoading(false);
        }
      }
    }

    void loadTokens();

    return () => controller.abort();
  }, []);

  async function handleGenerate() {
    setSubmitting(true);
    setErrorMessage(null);

    try {
      const payload = await generateToken();
      setNewToken(payload.token);
      setTokens((current) => [payload.api_token, ...current]);
    } catch (error: unknown) {
      setErrorMessage(errorMessageFrom(error, "API key generation failed."));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="aq-work-view">
      <div className="aq-content-head aq-content-head-pipelines">
        <div>
          <p className="aq-content-eyebrow">Settings</p>
          <h1 className="aq-content-title">API tokens</h1>
        </div>
        <p className="aq-content-summary">
          Generate agent credentials from an authenticated browser session. New
          secrets are shown once and never appear on the login screen.
        </p>
      </div>

      <div className="aq-pipelines-readonly">
        <span className="aq-pipelines-readonly-kicker">auth-gated</span>
        <span>
          Token generation is the only settings write exposed in the web UI.
          Work mutations still stay in CLI, REST, and MCP.
        </span>
      </div>

      <div className="aq-work-toolbar">
        <div className="aq-work-toolbar-meta">
          <span className="aq-mono aq-mute">
            {tokens.length} token{tokens.length === 1 ? "" : "s"} visible
          </span>
        </div>
        <button
          className="aq-secondary-button"
          disabled={submitting}
          onClick={handleGenerate}
          type="button"
        >
          {submitting ? "Generating..." : "Generate API key"}
        </button>
      </div>

      {errorMessage ? (
        <div className="aq-pipelines-state is-error" role="alert">
          <p className="aq-auth-kicker">Token failure</p>
          <h2 className="aq-state-title">Token settings need attention</h2>
          <p className="aq-state-copy">{errorMessage}</p>
        </div>
      ) : null}

      {newToken ? (
        <article className="aq-detail">
          <p className="aq-auth-kicker">One-time secret</p>
          <h2 className="aq-detail-title">New API key generated</h2>
          <p className="aq-detail-prose">
            This value is shown once. Store it before leaving this page.
          </p>
          <code className="aq-activity-command">{newToken}</code>
        </article>
      ) : null}

      {loading ? (
        <div className="aq-pipelines-state">
          <p className="aq-auth-kicker">Loading tokens</p>
          <h2 className="aq-state-title">Reading current token metadata</h2>
          <p className="aq-state-copy">
            Secret material is never returned for existing tokens.
          </p>
        </div>
      ) : tokens.length === 0 ? (
        <div className="aq-empty aq-empty-pipelines">
          <span className="aq-mono aq-mute">{"// no API tokens issued yet"}</span>
        </div>
      ) : (
        <section className="aq-table-work" aria-label="API tokens">
          <div className="aq-work-head aq-mono aq-mute">
            <div>prefix</div>
            <div>scopes</div>
            <div>created</div>
            <div>status</div>
            <div>expires</div>
            <div>updated</div>
            <div>id</div>
          </div>
          {tokens.map((token) => (
            <div className="aq-table-row" key={token.id}>
              <div className="aq-mono">{token.token_prefix}</div>
              <div>{token.scopes.length ? token.scopes.join(", ") : "default"}</div>
              <div className="aq-mono aq-mute">{formatTimestamp(token.created_at)}</div>
              <div>
                <span className={`aq-tone aq-tone-${token.revoked_at ? "warn" : "ok"}`}>
                  {token.revoked_at ? "revoked" : "active"}
                </span>
              </div>
              <div className="aq-mono aq-mute">
                {token.expires_at ? formatTimestamp(token.expires_at) : "never"}
              </div>
              <div className="aq-mono aq-mute">{formatTimestamp(token.updated_at)}</div>
              <div className="aq-mono aq-mute">{token.id.slice(0, 8)}</div>
            </div>
          ))}
        </section>
      )}
    </div>
  );
}

async function fetchTokens(signal: AbortSignal): Promise<TokenListResponse> {
  const response = await fetch("/api/tokens", {
    cache: "no-store",
    signal,
  });
  const payload = (await response.json().catch(() => null)) as
    | TokenListResponse
    | { error?: string }
    | null;

  if (!response.ok || payload === null || !("tokens" in payload)) {
    throw new Error(
      payload && "error" in payload && typeof payload.error === "string"
        ? payload.error
        : "Token request failed.",
    );
  }

  return payload;
}

async function generateToken(): Promise<TokenCreateResponse> {
  const response = await fetch("/api/tokens", {
    method: "POST",
    cache: "no-store",
  });
  const payload = (await response.json().catch(() => null)) as
    | TokenCreateResponse
    | { error?: string; message?: string }
    | null;

  if (!response.ok || payload === null || !("token" in payload)) {
    throw new Error(
      payload && "error" in payload && typeof payload.error === "string"
        ? payload.error
        : payload && "message" in payload && typeof payload.message === "string"
          ? payload.message
          : "Token generation failed.",
    );
  }

  return payload;
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

function errorMessageFrom(error: unknown, fallback: string) {
  return error instanceof Error ? error.message : fallback;
}
