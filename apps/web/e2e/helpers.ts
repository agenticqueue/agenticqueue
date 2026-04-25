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

const EMPTY_WORK_PAYLOAD = {
  generated_at: "2026-04-21T14:05:00.000Z",
  count: 0,
  items: [],
};

const EMPTY_DECISIONS_PAYLOAD = {
  generated_at: "2026-04-21T14:05:00.000Z",
  count: 0,
  items: [],
};

const EMPTY_ANALYTICS_PAYLOAD = {
  generated_at: "2026-04-21T14:05:00.000Z",
  window: {
    key: "90d",
    days: 90,
    start_at: "2026-01-21T14:05:00.000Z",
    end_at: "2026-04-21T14:05:00.000Z",
  },
  cycle_time: [],
  blocked_heatmap: [],
  handoff_latency_histogram: [],
  handoff_latency_by_actor: [],
  retrieval_precision: {
    sample_size: 0,
    precision_at_5: 0,
    precision_at_10: 0,
    note: "No retrieval samples yet.",
  },
  agent_success_rates: [],
  review_load: [],
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
  const token = options.token ?? "aq_live_playwright_token";

  await page.context().addCookies([
    {
      name: "aq_session",
      value: "playwright",
      url: "http://127.0.0.1:3005",
    },
  ]);

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

export async function mockShellReadApis(
  page: Page,
  options: {
    analyticsPayload?: unknown;
    decisionsPayload?: unknown;
    decisionLineageById?: Record<string, unknown>;
  } = {},
) {
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

  await page.route("**/api/v1/work**", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      json: EMPTY_WORK_PAYLOAD,
      status: 200,
    });
  });

  await page.route("**/api/v1/decisions**", async (route) => {
    const pathname = new URL(route.request().url()).pathname;
    const lineageMatch = pathname.match(/\/api\/v1\/decisions\/([^/]+)\/lineage$/);

    if (lineageMatch) {
      const decisionId = decodeURIComponent(lineageMatch[1] ?? "");
      await route.fulfill({
        contentType: "application/json",
        json:
          options.decisionLineageById?.[decisionId] ?? {
            generated_at: "2026-04-21T14:05:00.000Z",
            decision_id: decisionId,
            nodes: [],
            edges: [],
          },
        status: 200,
      });
      return;
    }

    await route.fulfill({
      contentType: "application/json",
      json: options.decisionsPayload ?? EMPTY_DECISIONS_PAYLOAD,
      status: 200,
    });
  });

  await page.route("**/api/v1/analytics/metrics**", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      json: options.analyticsPayload ?? EMPTY_ANALYTICS_PAYLOAD,
      status: 200,
    });
  });
}

export async function openAuthedView(
  page: Page,
  path: string,
  options: {
    analyticsPayload?: unknown;
    workPayload?: unknown;
    decisionsPayload?: unknown;
    decisionLineageById?: Record<string, unknown>;
  } = {},
) {
  await seedAuthenticatedSession(page);
  await mockShellReadApis(page, {
    analyticsPayload: options.analyticsPayload,
    decisionsPayload: options.decisionsPayload,
    decisionLineageById: options.decisionLineageById,
  });

  if (options.workPayload !== undefined) {
    await page.route("**/api/v1/work**", async (route) => {
      await route.fulfill({
        contentType: "application/json",
        json: options.workPayload,
        status: 200,
      });
    });
  }

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
