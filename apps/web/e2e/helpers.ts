import type { Page } from "@playwright/test";

type SessionPayload = {
  actor: {
    id: string;
    handle: string;
    actor_type: string;
    display_name: string;
  };
  tokenCount: number;
  apiBaseUrl: string;
};

const SESSION_TOKEN_KEY = "aq:web:api-token";
const PERSIST_TOKEN_KEY = "aq:web:remember-token";

const DEFAULT_SESSION_PAYLOAD: SessionPayload = {
  actor: {
    id: "actor-1",
    handle: "codex-hourly",
    actor_type: "admin",
    display_name: "Codex Runner",
  },
  tokenCount: 1,
  apiBaseUrl: "http://127.0.0.1:8010",
};

const EMPTY_PIPELINES_PAYLOAD = {
  state: "in_progress",
  count: 0,
  generated_at: "2026-04-21T14:05:00.000Z",
  pipelines: [],
};

const EMPTY_LEARNINGS_PAYLOAD = {
  query: "",
  count: 0,
  generated_at: "2026-04-21T14:05:00.000Z",
  items: [],
};

export async function seedAuthenticatedSession(
  page: Page,
  options: {
    remember?: boolean;
    sessionPayload?: SessionPayload;
    sessionStatus?: number;
    sessionJson?: unknown;
    token?: string;
  } = {},
) {
  const remember = options.remember ?? false;
  const token = options.token ?? "aq__playwright_token";

  await page.addInitScript(
    ({ apiToken, persist }) => {
      window.localStorage.setItem(
        "aq:web:remember-token",
        persist ? "true" : "false",
      );
      window.localStorage.removeItem("aq:web:api-token");
      window.sessionStorage.removeItem("aq:web:api-token");
      if (persist) {
        window.localStorage.setItem("aq:web:api-token", apiToken);
      } else {
        window.sessionStorage.setItem("aq:web:api-token", apiToken);
      }
    },
    { apiToken: token, persist: remember },
  );

  const status = options.sessionStatus ?? 200;
  const json = options.sessionJson ?? options.sessionPayload ?? DEFAULT_SESSION_PAYLOAD;

  await page.route("**/api/session", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      json,
      status,
    });
  });
}

export async function mockShellReadApis(page: Page) {
  await page.route("**/api/v1/pipelines**", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      json: EMPTY_PIPELINES_PAYLOAD,
      status: 200,
    });
  });

  await page.route("**/api/v1/learnings/search**", async (route) => {
    const query = new URL(route.request().url()).searchParams.get("query") ?? "";
    await route.fulfill({
      contentType: "application/json",
      json: {
        ...EMPTY_LEARNINGS_PAYLOAD,
        query,
      },
      status: 200,
    });
  });
}

export async function openAuthedView(page: Page, path: string) {
  await seedAuthenticatedSession(page);
  await mockShellReadApis(page);
  await page.goto(path);
}

export async function expectClearedStoredToken(page: Page) {
  return page.evaluate(
    ({
      persistKey,
      sessionKey,
    }: {
      persistKey: string;
      sessionKey: string;
    }) => ({
      persistedPreference: window.localStorage.getItem(persistKey),
      localToken: window.localStorage.getItem(sessionKey),
      sessionToken: window.sessionStorage.getItem(sessionKey),
    }),
    {
      persistKey: PERSIST_TOKEN_KEY,
      sessionKey: SESSION_TOKEN_KEY,
    },
  );
}
