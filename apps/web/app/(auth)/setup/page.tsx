"use client";

import { FormEvent, ReactNode, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";

import { SplitPitch } from "../_components/split-pitch";
import { StatusActivity } from "../_components/status-activity";
import styles from "./setup.module.css";

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const PASSWORD_MIN_LENGTH = 12;
const STRENGTH_LABELS = ["", "Weak", "Fair", "Good", "Strong"] as const;

type FieldKey = "email" | "password" | "confirm";
type FieldErrors = Partial<Record<FieldKey, string>>;

type BootstrapStatusResponse = {
  needs_bootstrap?: boolean;
};

type BootstrapAdminResponse = {
  user?: {
    email?: string;
  };
  first_token?: string;
};

type ApiErrorPayload = {
  message?: string;
  details?: unknown;
  error?: {
    message?: string;
    details?: unknown;
  };
};

type DoneState = {
  email: string;
  firstToken: string;
};

function scorePassword(value: string) {
  let score = 0;
  if (value.length >= 8) score++;
  if (value.length >= 12) score++;
  if (/[A-Z]/.test(value) && /[a-z]/.test(value)) score++;
  if (/\d/.test(value) && /[^A-Za-z0-9]/.test(value)) score++;
  return Math.min(score, 4);
}

function validateSetupForm({
  email,
  password,
  confirm,
}: Record<FieldKey, string>) {
  const nextErrors: FieldErrors = {};
  if (!EMAIL_RE.test(email)) {
    nextErrors.email = "Enter a valid email address.";
  }
  if (password.length < PASSWORD_MIN_LENGTH) {
    nextErrors.password = "Password must be at least 12 characters.";
  }
  if (confirm !== password) {
    nextErrors.confirm = "Passwords don't match.";
  }
  return nextErrors;
}

function readErrorMessage(payload: ApiErrorPayload | null) {
  return payload?.error?.message ?? payload?.message ?? null;
}

function readErrorDetails(payload: ApiErrorPayload | null) {
  return payload?.error?.details ?? payload?.details ?? null;
}

function isFieldKey(value: unknown): value is FieldKey {
  return (
    value === "email" ||
    value === "password" ||
    value === "confirm"
  );
}

function mapValidationDetails(details: unknown) {
  const nextErrors: FieldErrors = {};
  if (!Array.isArray(details)) return nextErrors;

  for (const detail of details) {
    if (typeof detail !== "object" || detail === null) continue;
    const record = detail as { loc?: unknown; msg?: unknown };
    if (!Array.isArray(record.loc)) continue;
    const field = record.loc.at(-1);
    if (isFieldKey(field)) {
      nextErrors[field] =
        typeof record.msg === "string"
          ? record.msg
          : "Check this field and try again.";
    }
  }
  return nextErrors;
}

function Field({
  id,
  label,
  hint,
  note,
  error,
  children,
}: {
  id: string;
  label: string;
  hint?: string;
  note?: string;
  error?: string;
  children: ReactNode;
}) {
  return (
    <div className="field">
      <div className="field-label-row">
        <label className="field-label" htmlFor={id}>
          {label}
        </label>
        {hint ? <span className="field-hint">{hint}</span> : null}
      </div>
      <div className={`input-wrap${error ? " has-error" : ""}`}>
        {children}
      </div>
      {note ? <div className="field-note">{note}</div> : null}
      {error ? (
        <div className="error" id={`${id}-error`}>
          {error}
        </div>
      ) : null}
    </div>
  );
}

function StrengthMeter({ value }: { value: string }) {
  const score = scorePassword(value);
  return (
    <>
      <div className="strength" aria-hidden="true">
        {[0, 1, 2, 3].map((index) => (
          <div
            className={`strength-seg${index < score ? ` on-${score}` : ""}`}
            key={index}
          />
        ))}
      </div>
      <div className="strength-label" aria-live="polite">
        {value ? STRENGTH_LABELS[score] : ""}
      </div>
    </>
  );
}

function LoadingPanel() {
  return (
    <SplitPitch variant="setup">
      <section className={styles.setupPanel} aria-busy="true">
        <div className="status-strip">
          <StatusActivity />
          <span>Checking setup status · <code>/setup</code></span>
        </div>

        <div className="heading">
          <h1>Checking first-run access</h1>
          <p className="status-copy">
            Waiting for the API to confirm whether this instance still needs its
            first admin account.
          </p>
        </div>
      </section>
    </SplitPitch>
  );
}

function DonePanel({
  done,
  onContinue,
}: {
  done: DoneState;
  onContinue: () => void;
}) {
  const [copied, setCopied] = useState(false);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(done.firstToken);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      setCopied(false);
    }
  };

  return (
    <SplitPitch variant="done">
      <section className={styles.setupPanel}>
        <div className="done-head">
          <span className="done-head-dot" />
          <span>Instance ready</span>
        </div>

        <div className="heading">
          <h1>You&apos;re in.</h1>
          <p>
            Signed in as <code>{done.email}</code>. Here&apos;s your first
            access token. Use this token to authenticate any agent via MCP
            server, <code>aq</code> CLI, or HTTP API.
          </p>
        </div>

        <div>
          <div className="field-label" style={{ marginBottom: 8 }}>
            First access token
          </div>
          <div className="token-box">
            <span className="token-value">{done.firstToken}</span>
            <button onClick={copy} type="button">
              {copied ? "copied" : "copy"}
            </button>
          </div>
        </div>

        <div className="warn-box">
          <span className="warn-mark">!</span>
          <span>
            <strong>Copy this now.</strong> AgenticQueue stores only the hash.
            This full token won&apos;t be shown again.
          </span>
        </div>

        <ol className="next-list">
          <li>
            <span className="num">01</span>
            <span>
              Paste into <code>.env</code> as <code>AQ_TOKEN</code>
            </span>
          </li>
          <li>
            <span className="num">02</span>
            <span>
              Configure your agent: AQ MCP server, <code>aq</code> CLI, or HTTP{" "}
              <code>/api</code>. All use this token as bearer auth.
            </span>
          </li>
          <li>
            <span className="num">03</span>
            <span>Create more tokens (one per agent) at Settings → API keys</span>
          </li>
        </ol>

        <button className="primary" onClick={onContinue} type="button">
          <span>Continue to dashboard</span>
          <span className="arrow">→</span>
        </button>
      </section>
    </SplitPitch>
  );
}

export default function SetupPage() {
  const router = useRouter();
  const [status, setStatus] = useState<"checking" | "ready">("checking");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [errors, setErrors] = useState<FieldErrors>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [done, setDone] = useState<DoneState | null>(null);

  useEffect(() => {
    let active = true;

    async function checkBootstrapStatus() {
      try {
        const response = await fetch("/api/auth/bootstrap_status", {
          cache: "no-store",
        });
        const payload = (await response.json().catch(() => null)) as
          | BootstrapStatusResponse
          | null;

        if (!active) return;
        if (!response.ok || payload?.needs_bootstrap !== true) {
          window.location.replace("/login");
          return;
        }
        setStatus("ready");
      } catch {
        if (!active) return;
        setFormError("Setup status is unavailable. Try again.");
        setStatus("ready");
      }
    }

    void checkBootstrapStatus();
    return () => {
      active = false;
    };
  }, [router]);

  const formValues = useMemo(
    () => ({ email, password, confirm }),
    [confirm, email, password],
  );

  const clearFieldError = (field: FieldKey) => {
    setErrors((current) => ({ ...current, [field]: undefined }));
    setFormError(null);
  };

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();

    const nextErrors = validateSetupForm(formValues);
    setErrors(nextErrors);
    setFormError(null);
    if (Object.keys(nextErrors).length > 0) return;

    setIsSubmitting(true);
    try {
      const response = await fetch("/api/auth/bootstrap_admin", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          email,
          password,
        }),
      });
      const payload = (await response.json().catch(() => null)) as
        | BootstrapAdminResponse
        | ApiErrorPayload
        | null;

      if (response.ok) {
        const successPayload = payload as BootstrapAdminResponse | null;
        if (
          typeof successPayload?.first_token !== "string" ||
          !successPayload.first_token.startsWith("aq_live_")
        ) {
          setFormError("Setup completed, but the token response was invalid.");
          return;
        }
        setDone({
          email: successPayload.user?.email ?? email,
          firstToken: successPayload.first_token,
        });
        return;
      }

      const errorPayload = payload as ApiErrorPayload | null;
      if (response.status === 401) {
        setFormError("Setup could not authenticate this request.");
        return;
      }
      if (response.status === 404 || response.status === 409) {
        setFormError("Setup has already been completed. Sign in instead.");
        return;
      }
      if (response.status === 422) {
        const validationErrors = mapValidationDetails(
          readErrorDetails(errorPayload),
        );
        setErrors(validationErrors);
        setFormError(
          Object.keys(validationErrors).length > 0
            ? "Check the highlighted setup fields."
            : "The server rejected the setup payload.",
        );
        return;
      }
      if (response.status === 503) {
        setFormError("Setup is not available on the server.");
        return;
      }

      setFormError(
        readErrorMessage(errorPayload) ?? "Setup failed. Try again.",
      );
    } finally {
      setIsSubmitting(false);
    }
  };

  if (done !== null) {
    return <DonePanel done={done} onContinue={() => router.replace("/")} />;
  }

  if (status === "checking") {
    return <LoadingPanel />;
  }

  return (
    <SplitPitch variant="setup">
      <section className={styles.setupPanel}>
        <div className="status-strip">
          <span className="status-dot" />
          <span>
            First run · no admin account yet · <code>/setup</code>
          </span>
        </div>

        <div className="heading">
          <h1>Set up this instance</h1>
          <p>
            Create the first admin account for your AgenticQueue deployment.
            Once this is done, the setup page locks itself.
          </p>
        </div>

        <div
          aria-label="First-run security"
          className="first-run-warning"
          role="note"
        >
          Complete setup before exposing this URL publicly.{" "}
          <a href="https://github.com/agenticqueue/agenticqueue#first-run-security">
            First-run security
          </a>
        </div>

        <form autoComplete="off" noValidate onSubmit={submit}>
          {formError ? <div className="form-error">{formError}</div> : null}
          <fieldset disabled={isSubmitting}>
            <Field
              error={errors.email}
              id="setup-email"
              label="Admin email"
              note="This email becomes the first admin account on your instance."
            >
              <input
                aria-describedby={errors.email ? "setup-email-error" : undefined}
                aria-invalid={Boolean(errors.email)}
                autoCapitalize="off"
                autoComplete="email"
                id="setup-email"
                inputMode="email"
                onChange={(event) => {
                  setEmail(event.target.value);
                  clearFieldError("email");
                }}
                placeholder="admin@example.com"
                spellCheck="false"
                type="email"
                value={email}
              />
            </Field>

            <Field
              error={errors.password}
              hint="min 12 characters"
              id="setup-password"
              label="Password"
            >
              <input
                aria-describedby={
                  errors.password ? "setup-password-error" : undefined
                }
                aria-invalid={Boolean(errors.password)}
                autoComplete="new-password"
                id="setup-password"
                onChange={(event) => {
                  setPassword(event.target.value);
                  clearFieldError("password");
                }}
                placeholder="••••••••••••"
                spellCheck="false"
                type={showPassword ? "text" : "password"}
                value={password}
              />
              <button
                className="reveal"
                onClick={() => setShowPassword((current) => !current)}
                type="button"
              >
                {showPassword ? "hide" : "show"}
              </button>
            </Field>
            <StrengthMeter value={password} />

            <Field
              error={errors.confirm}
              id="setup-confirm"
              label="Confirm password"
            >
              <input
                aria-describedby={
                  errors.confirm ? "setup-confirm-error" : undefined
                }
                aria-invalid={Boolean(errors.confirm)}
                autoComplete="new-password"
                id="setup-confirm"
                onChange={(event) => {
                  setConfirm(event.target.value);
                  clearFieldError("confirm");
                }}
                placeholder="••••••••••••"
                spellCheck="false"
                type={showPassword ? "text" : "password"}
                value={confirm}
              />
            </Field>

            <button className="primary" disabled={isSubmitting} type="submit">
              <span>
                {isSubmitting ? "Creating admin…" : "Create admin account"}
              </span>
              <span className="arrow">→</span>
            </button>
          </fieldset>
        </form>
      </section>
    </SplitPitch>
  );
}
