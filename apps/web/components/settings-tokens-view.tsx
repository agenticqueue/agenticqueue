"use client";

import { FormEvent, useEffect, useState } from "react";

type ApiToken = {
  id: string;
  name: string;
  token_preview: string;
  created_at: string;
  last_used_at: string | null;
};

type TokenListResponse = {
  tokens?: ApiToken[];
  error?: string;
  message?: string;
};

type TokenCreateResponse = ApiToken & {
  token?: string;
  error?: string;
  message?: string;
};

type CreateModalState =
  | { phase: "form"; name: string; error: string | null; submitting: boolean }
  | {
      phase: "reveal";
      token: string;
      tokenMeta: ApiToken;
      copied: boolean;
      canContinue: boolean;
    };

function formatDate(value: string | null) {
  if (!value) {
    return "Never";
  }

  return new Intl.DateTimeFormat("en", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

function readError(payload: { error?: string; message?: string } | null) {
  return payload?.error ?? payload?.message ?? "Token request failed.";
}

export function SettingsTokensView() {
  const [tokens, setTokens] = useState<ApiToken[]>([]);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [error, setError] = useState<string | null>(null);
  const [modal, setModal] = useState<CreateModalState | null>(null);

  async function loadTokens() {
    setStatus("loading");
    setError(null);
    try {
      const response = await fetch("/api/auth/tokens", { cache: "no-store" });
      const payload = (await response.json().catch(() => null)) as
        | TokenListResponse
        | null;

      if (!response.ok || !Array.isArray(payload?.tokens)) {
        throw new Error(readError(payload));
      }

      setTokens(payload.tokens);
      setStatus("ready");
    } catch (caught) {
      setStatus("error");
      setError(caught instanceof Error ? caught.message : "Token request failed.");
    }
  }

  useEffect(() => {
    void loadTokens();
  }, []);

  useEffect(() => {
    if (modal?.phase !== "reveal") {
      return;
    }

    const timer = window.setTimeout(() => {
      setModal((current) =>
        current?.phase === "reveal"
          ? { ...current, canContinue: true }
          : current,
      );
    }, 1000);
    return () => window.clearTimeout(timer);
  }, [modal?.phase]);

  const openCreate = () => {
    setModal({ phase: "form", name: "", error: null, submitting: false });
  };

  const createToken = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (modal?.phase !== "form") {
      return;
    }

    const name = modal.name.trim();
    if (!name) {
      setModal({ ...modal, error: "Token name is required." });
      return;
    }

    setModal({ ...modal, submitting: true, error: null });
    try {
      const response = await fetch("/api/auth/tokens", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
      });
      const payload = (await response.json().catch(() => null)) as
        | TokenCreateResponse
        | null;

      if (!response.ok || typeof payload?.token !== "string") {
        throw new Error(readError(payload));
      }

      const tokenMeta: ApiToken = {
        id: payload.id,
        name: payload.name,
        token_preview: payload.token_preview,
        created_at: payload.created_at,
        last_used_at: payload.last_used_at,
      };
      setTokens((current) => [...current, tokenMeta]);
      setModal({
        phase: "reveal",
        token: payload.token,
        tokenMeta,
        copied: false,
        canContinue: false,
      });
    } catch (caught) {
      setModal({
        phase: "form",
        name,
        submitting: false,
        error: caught instanceof Error ? caught.message : "Token request failed.",
      });
    }
  };

  const copyToken = async () => {
    if (modal?.phase !== "reveal") {
      return;
    }

    try {
      await navigator.clipboard.writeText(modal.token);
    } catch {
      // Keep the reveal flow usable in browser contexts without clipboard grants.
    }
    setModal({ ...modal, copied: true });
  };

  const closeReveal = () => {
    setModal(null);
  };

  const revokeToken = async (token: ApiToken) => {
    const response = await fetch(`/api/auth/tokens/${token.id}`, {
      method: "DELETE",
    });
    if (!response.ok) {
      setError("Token revoke failed.");
      return;
    }
    setTokens((current) => current.filter((item) => item.id !== token.id));
  };

  return (
    <>
      <div className="aq-content-head">
        <div>
          <p className="aq-content-eyebrow">Settings / API keys</p>
          <h1 className="aq-content-title">API keys</h1>
        </div>
        <button className="aq-secondary-button" onClick={openCreate} type="button">
          New API key
        </button>
      </div>

      {error ? (
        <div className="aq-auth-error" role="alert">
          {error}
        </div>
      ) : null}

      <div className="aq-token-table" aria-busy={status === "loading"}>
        <div className="aq-token-head">
          <span>Name</span>
          <span>Preview</span>
          <span>Created</span>
          <span>Last used</span>
          <span>Action</span>
        </div>
        {tokens.map((token) => (
          <div className="aq-token-row" key={token.id}>
            <strong>{token.name}</strong>
            <code>{token.token_preview}</code>
            <span>{formatDate(token.created_at)}</span>
            <span>{formatDate(token.last_used_at)}</span>
            <button
              aria-label={`Revoke ${token.name}`}
              className="aq-secondary-button aq-danger-button"
              onClick={() => void revokeToken(token)}
              type="button"
            >
              Revoke
            </button>
          </div>
        ))}
        {status === "ready" && tokens.length === 0 ? (
          <div className="aq-token-empty">No active API keys.</div>
        ) : null}
        {status === "loading" ? (
          <div className="aq-token-empty">Loading API keys...</div>
        ) : null}
      </div>

      {modal ? (
        <div className="aq-modal-backdrop" role="presentation">
          <div
            aria-labelledby="create-token-title"
            aria-modal="true"
            className="aq-token-modal"
            role="dialog"
          >
            {modal.phase === "form" ? (
              <form className="aq-token-form" onSubmit={createToken}>
                <div>
                  <p className="aq-content-eyebrow">New key</p>
                  <h2 id="create-token-title">Create API key</h2>
                </div>
                <label className="aq-auth-label" htmlFor="token-name">
                  Token name
                </label>
                <div className="aq-auth-input-wrap">
                  <input
                    className="aq-auth-input"
                    id="token-name"
                    onChange={(event) =>
                      setModal({ ...modal, name: event.target.value, error: null })
                    }
                    value={modal.name}
                  />
                </div>
                {modal.error ? (
                  <p className="aq-auth-error" role="alert">
                    {modal.error}
                  </p>
                ) : null}
                <div className="aq-token-actions">
                  <button
                    className="aq-secondary-button"
                    disabled={modal.submitting}
                    type="submit"
                  >
                    {modal.submitting ? "Creating..." : "Create API key"}
                  </button>
                  <button
                    className="aq-secondary-button"
                    onClick={() => setModal(null)}
                    type="button"
                  >
                    Cancel
                  </button>
                </div>
              </form>
            ) : (
              <div className="aq-token-form">
                <div>
                  <p className="aq-content-eyebrow">{modal.tokenMeta.name}</p>
                  <h2 id="create-token-title">Copy this token</h2>
                </div>
                <div className="aq-token-reveal">
                  <code>{modal.token}</code>
                  <button
                    className="aq-secondary-button"
                    onClick={() => void copyToken()}
                    type="button"
                  >
                    {modal.copied ? "Copied" : "Copy token"}
                  </button>
                </div>
                <div className="aq-token-warning">
                  This token will not be shown again.
                </div>
                <div className="aq-token-actions">
                  <button
                    className="aq-secondary-button"
                    disabled={!modal.canContinue}
                    onClick={closeReveal}
                    type="button"
                  >
                    Continue
                  </button>
                </div>
              </div>
            )}
          </div>
        </div>
      ) : null}
    </>
  );
}
