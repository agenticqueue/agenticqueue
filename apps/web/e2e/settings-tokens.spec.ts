import { expect, test } from "@playwright/test";

import { mockShellReadApis } from "./helpers";

const SESSION_PAYLOAD = {
  actor: {
    id: "actor-1",
    handle: "admin",
    actor_type: "admin",
    display_name: "Admin",
  },
  tokenCount: 1,
  apiBaseUrl: "http://127.0.0.1:8010",
};

test("unauthenticated token settings redirects to login", async ({ page }) => {
  await page.route("**/api/session", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      json: { error: "Local user session required." },
      status: 401,
    });
  });

  await page.goto("/settings/tokens");

  await expect(page).toHaveURL(/\/login$/);
  await expect(
    page.getByRole("heading", { level: 1, name: "Sign in to AgenticQueue" }),
  ).toBeVisible();
});

test("authenticated user can generate API keys only from settings tokens", async ({
  page,
}) => {
  await mockShellReadApis(page);
  await page.route("**/api/session", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      json: SESSION_PAYLOAD,
      status: 200,
    });
  });
  await page.route("**/api/tokens", async (route) => {
    if (route.request().method() === "POST") {
      await route.fulfill({
        contentType: "application/json",
        json: {
          token: "aq__generated_secret",
          api_token: {
            id: "token-2",
            token_prefix: "aq__generated",
            scopes: ["admin"],
            created_at: "2026-04-23T20:00:00.000Z",
            updated_at: "2026-04-23T20:00:00.000Z",
          },
        },
        status: 201,
      });
      return;
    }

    await route.fulfill({
      contentType: "application/json",
      json: {
        actor: SESSION_PAYLOAD.actor,
        tokens: [
          {
            id: "token-1",
            token_prefix: "aq__existing",
            scopes: ["admin"],
            created_at: "2026-04-23T19:00:00.000Z",
            updated_at: "2026-04-23T19:00:00.000Z",
          },
        ],
      },
      status: 200,
    });
  });

  await page.goto("/settings/tokens");

  await expect(page.getByRole("heading", { level: 1, name: "API tokens" })).toBeVisible();
  await expect(page.getByText("aq__existing")).toBeVisible();
  await page.getByRole("button", { name: "Generate API key" }).click();
  await expect(page.getByText("aq__generated_secret")).toBeVisible();

  await page.goto("/login");
  await expect(page.getByText(/api key|api token|bearer token/i)).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Generate API key" })).toHaveCount(0);
});
