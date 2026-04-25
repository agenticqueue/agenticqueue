"use client";

import { FormEvent, ReactNode, useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { SplitPitch } from "../(auth)/_components/split-pitch";
import styles from "../(auth)/setup/setup.module.css";

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const REMEMBER_EMAIL_KEY = "aq_email";

type FieldKey = "email" | "password";
type FieldErrors = Partial<Record<FieldKey, string>>;

type SessionPayload = {
  apiBaseUrl?: string;
  error?: string;
  message?: string;
  status?: number;
  user?: {
    email?: string;
    is_admin?: boolean;
  };
};

type BootstrapStatusResponse = {
  needs_bootstrap?: boolean;
};

function readErrorMessage(payload: SessionPayload | null) {
  return payload?.error ?? payload?.message ?? null;
}

function Field({
  id,
  label,
  hint,
  error,
  children,
}: {
  id: string;
  label: string;
  hint?: ReactNode;
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
      {error ? (
        <div className="error" id={`${id}-error`}>
          {error}
        </div>
      ) : null}
    </div>
  );
}

export default function LoginPage() {
  const router = useRouter();
  const [status, setStatus] = useState<"checking" | "ready">("checking");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [remember, setRemember] = useState(true);
  const [showPassword, setShowPassword] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [errors, setErrors] = useState<FieldErrors>({});
  const [formError, setFormError] = useState<string | null>(null);

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
        if (response.ok && payload?.needs_bootstrap === true) {
          window.location.replace("/setup");
          return;
        }
        setStatus("ready");
      } catch {
        if (!active) return;
        setStatus("ready");
      }
    }

    void checkBootstrapStatus();
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    const rememberedEmail = window.localStorage.getItem(REMEMBER_EMAIL_KEY);
    if (rememberedEmail) {
      setEmail(rememberedEmail);
    }
  }, []);

  const clearFieldError = (field: FieldKey) => {
    setErrors((current) => ({ ...current, [field]: undefined }));
    setFormError(null);
  };

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();

    const nextErrors: FieldErrors = {};
    if (!EMAIL_RE.test(email)) {
      nextErrors.email = "Enter a valid email address.";
    }
    if (!password) {
      nextErrors.password = "Enter your password.";
    }

    setErrors(nextErrors);
    setFormError(null);
    if (Object.keys(nextErrors).length > 0) return;

    setIsSubmitting(true);
    try {
      const response = await fetch("/api/session", {
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
        | SessionPayload
        | null;

      if (response.ok && payload?.user) {
        if (remember) {
          window.localStorage.setItem(REMEMBER_EMAIL_KEY, email);
        } else {
          window.localStorage.removeItem(REMEMBER_EMAIL_KEY);
        }
        router.replace("/pipelines");
        return;
      }

      if (response.status === 401) {
        setErrors({ password: "Email or password is incorrect." });
        return;
      }

      if (response.status === 422) {
        setFormError("Check the highlighted sign-in fields.");
        return;
      }

      setFormError(readErrorMessage(payload) ?? "Sign-in failed. Try again.");
    } catch {
      setFormError("Sign-in service is unavailable. Try again.");
    } finally {
      setIsSubmitting(false);
    }
  };

  if (status !== "ready") {
    return null;
  }

  return (
    <SplitPitch variant="login">
      <section className={styles.setupPanel}>
        <div className="status-strip">
          <span className="status-dot signed-in" />
          <span>
            Ready · admin exists · <code>/login</code>
          </span>
        </div>

        <div className="heading">
          <h1>Sign in</h1>
          <p>
            Welcome back. Sign in to view pipelines, work, decisions and
            learnings.
          </p>
        </div>

        <form autoComplete="off" noValidate onSubmit={submit}>
          {formError ? (
            <div className="form-error" role="alert">
              {formError}
            </div>
          ) : null}
          <fieldset disabled={isSubmitting}>
            <Field error={errors.email} id="login-email" label="Email">
              <input
                aria-describedby={errors.email ? "login-email-error" : undefined}
                aria-invalid={Boolean(errors.email)}
                autoCapitalize="off"
                autoComplete="email"
                id="login-email"
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
              hint={
                <a href="#" onClick={(event) => event.preventDefault()}>
                  Forgot?
                </a>
              }
              id="login-password"
              label="Password"
            >
              <input
                aria-describedby={
                  errors.password ? "login-password-error" : undefined
                }
                aria-invalid={Boolean(errors.password)}
                autoComplete="current-password"
                id="login-password"
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
                aria-pressed={showPassword}
                className="reveal"
                onClick={() => setShowPassword((current) => !current)}
                type="button"
              >
                {showPassword ? "hide" : "show"}
              </button>
            </Field>

            <label className="remember">
              <input
                checked={remember}
                onChange={(event) => setRemember(event.target.checked)}
                type="checkbox"
              />
              <span>Remember me on this device</span>
            </label>

            <button className="primary" disabled={isSubmitting} type="submit">
              <span>{isSubmitting ? "Signing in…" : "Sign in"}</span>
              <span className="arrow">→</span>
            </button>
          </fieldset>
        </form>

        <div className="divider" />
        <div className="foot">
          <span>
            Agent or script? Use a <a href="#">bearer token</a>, not this form.
          </span>
          <a href="#">Help</a>
        </div>
      </section>
    </SplitPitch>
  );
}
