import { expect, test } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import path from "node:path";

import type { Page, TestInfo } from "@playwright/test";

import { mockShellReadApis, seedAuthenticatedSession } from "./helpers";

const E2E_AUTH_API_URL = "http://127.0.0.1:3127";

async function setBootstrapState(needsBootstrap: boolean) {
  const response = await fetch(`${E2E_AUTH_API_URL}/__aq_e2e/state`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ needs_bootstrap: needsBootstrap }),
  });
  expect(response.ok).toBe(true);
}

async function attachScreenshot(page: Page, testInfo: TestInfo, name: string) {
  const screenshotPath = path.join(
    process.cwd(),
    "test-results",
    "aq300",
    `${name}.png`,
  );
  await mkdir(path.dirname(screenshotPath), { recursive: true });
  await page.screenshot({ fullPage: true, path: screenshotPath });
  await testInfo.attach(name, {
    path: screenshotPath,
    contentType: "image/png",
  });
}

test.afterEach(async () => {
  await setBootstrapState(false);
});

test("auth-entry-fresh redirects root to setup", async ({ request }) => {
  await setBootstrapState(true);

  const response = await request.get("/", { maxRedirects: 0 });

  expect(response.status()).toBe(307);
  const location = new URL(response.headers().location ?? "");
  expect(location.pathname).toBe("/setup");
});

test("auth-entry-no-session redirects root to login with next path", async ({
  request,
}) => {
  await setBootstrapState(false);

  const response = await request.get("/", { maxRedirects: 0 });

  expect(response.status()).toBe(307);
  const location = new URL(response.headers().location ?? "");
  expect(`${location.pathname}${location.search}`).toBe("/login?next=%2F");
});

test("auth-entry-authed renders dashboard without redirect", async ({
  page,
}, testInfo) => {
  await setBootstrapState(false);
  await seedAuthenticatedSession(page);
  await mockShellReadApis(page);

  const response = await page.goto("/");

  expect(response?.status()).toBe(200);
  await expect(
    page.getByRole("heading", { level: 1, name: "Pipelines" }),
  ).toBeVisible();
  await attachScreenshot(page, testInfo, "dashboard-after-auth");
});

test("login-mount-guard redirects login to setup on a fresh instance", async ({
  page,
}, testInfo) => {
  await setBootstrapState(true);

  await page.goto("/login");

  await expect(page).toHaveURL(/\/setup$/);
  await expect(
    page.getByRole("heading", { level: 1, name: "Set up this instance" }),
  ).toBeVisible();
  await attachScreenshot(page, testInfo, "setup-via-login-redirect");
});
