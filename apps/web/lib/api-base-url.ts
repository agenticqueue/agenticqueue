export const DEFAULT_API_BASE_URL = "http://127.0.0.1:8010";
export const CANONICAL_API_BASE_URL_ENV_VAR = "AQ_API_BASE_URL";
export const DEPRECATED_API_BASE_URL_ENV_VARS = [
  "AQ_API_URL",
  "AGENTICQUEUE_API_BASE_URL",
  "NEXT_PUBLIC_AGENTICQUEUE_API_BASE_URL",
] as const;

type ApiBaseUrlEnv = Record<string, string | undefined> & {
  AQ_API_BASE_URL?: string;
  AQ_API_URL?: string;
  AGENTICQUEUE_API_BASE_URL?: string;
  NEXT_PUBLIC_AGENTICQUEUE_API_BASE_URL?: string;
};

function normalizeApiBaseUrl(value: string | undefined) {
  const trimmed = value?.trim();
  return trimmed ? trimmed : null;
}

export function getApiBaseUrl(env: ApiBaseUrlEnv = process.env) {
  return (
    normalizeApiBaseUrl(env.AQ_API_BASE_URL) ??
    normalizeApiBaseUrl(env.AQ_API_URL) ??
    normalizeApiBaseUrl(env.AGENTICQUEUE_API_BASE_URL) ??
    normalizeApiBaseUrl(env.NEXT_PUBLIC_AGENTICQUEUE_API_BASE_URL) ??
    DEFAULT_API_BASE_URL
  );
}
