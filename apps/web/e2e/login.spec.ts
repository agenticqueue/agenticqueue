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

test("renders username and passcode login with submit only", async ({ page }) => {
  await page.route("**/api/session", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      json: { error: "Local user session required." },
      status: 401,
    });
  });

  await page.goto("/login");

  const form = page.locator("form[aria-label='Sign in to AgenticQueue']");
  await expect(
    page.getByRole("heading", { level: 1, name: "Sign in to AgenticQueue" }),
  ).toBeVisible();
  await expect(form.getByLabel("Username")).toBeVisible();
  await expect(form.getByLabel("Passcode")).toBeVisible();
  await expect(form.locator("input, textarea, button")).toHaveCount(3);
  await expect(form.locator("textarea")).toHaveCount(0);
  await expect(form.locator("[name='apiKey'], [name='api_key'], #api-token")).toHaveCount(0);
  await expect(page.getByText(/api key|api token|bearer token/i)).toHaveCount(0);
});

test("no signup links appear on the login page", async ({ page }) => {
  await page.route("**/api/session", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      json: { error: "Local user session required." },
      status: 401,
    });
  });

  await page.goto("/login");

  await expect(page.getByRole("link", { name: /sign up|register|forgot password/i })).toHaveCount(0);
  await expect(page.getByText(/sign up|register|forgot password/i)).toHaveCount(0);
});

test("wrong passcode returns a clean login error", async ({ page }) => {
  await page.route("**/api/session", async (route) => {
    if (route.request().method() === "POST") {
      await route.fulfill({
        contentType: "application/json",
        json: { error: "Invalid username or passcode" },
        status: 401,
      });
      return;
    }

    await route.fulfill({
      contentType: "application/json",
      json: { error: "Local user session required." },
      status: 401,
    });
  });

  await page.goto("/login");
  await page.getByLabel("Username").fill("admin");
  await page.getByLabel("Passcode").fill("wrong-passcode");
  await page.getByRole("button", { name: "Sign in" }).click();

  await expect(page.locator(".aq-auth-error")).toContainText(
    "Invalid username or passcode",
  );
  await expect(page).toHaveURL(/\/login$/);
});

test("successful login redirects to project view without local storage session", async ({
  page,
}) => {
  await mockShellReadApis(page);
  let signedIn = false;

  await page.route("**/api/session", async (route) => {
    if (route.request().method() === "POST") {
      signedIn = true;
      await route.fulfill({
        contentType: "application/json",
        headers: {
          "Set-Cookie":
            "aq_session=opaque-session; Path=/; Max-Age=604800; HttpOnly; Secure; SameSite=Lax",
        },
        json: SESSION_PAYLOAD,
        status: 200,
      });
      return;
    }

    if (signedIn) {
      await route.fulfill({
        contentType: "application/json",
        json: SESSION_PAYLOAD,
        status: 200,
      });
      return;
    }

    await route.fulfill({
      contentType: "application/json",
      json: { error: "Local user session required." },
      status: 401,
    });
  });

  await page.goto("/login");
  await page.getByLabel("Username").fill("admin");
  await page.getByLabel("Passcode").fill("correct-passcode");
  const responsePromise = page.waitForResponse("**/api/session");
  await page.getByRole("button", { name: "Sign in" }).click();
  const response = await responsePromise;

  expect(response.ok()).toBe(true);
  await expect(page).toHaveURL(/\/pipelines$/);
  await expect(page.getByRole("heading", { level: 1, name: "Pipelines" })).toBeVisible();

  const cookies = await page.context().cookies();
  expect(cookies).toEqual(
    expect.arrayContaining([
      expect.objectContaining({
        httpOnly: true,
        name: "aq_session",
        secure: true,
      }),
    ]),
  );

  const stored = await page.evaluate(() => ({
    local: { ...window.localStorage },
    session: { ...window.sessionStorage },
  }));
  expect(Object.keys(stored.local).filter((key) => /session|token|jwt/i.test(key))).toEqual([]);
  expect(Object.keys(stored.session).filter((key) => /session|token|jwt/i.test(key))).toEqual([]);
});
