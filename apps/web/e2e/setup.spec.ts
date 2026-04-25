import { mkdir } from "node:fs/promises";
import path from "node:path";

import { expect, test, type Page, type TestInfo } from "@playwright/test";

test.use({ viewport: { height: 800, width: 1280 } });

async function mockNeedsBootstrap(page: Page) {
  await page.route("**/api/auth/bootstrap_status", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      json: { needs_bootstrap: true },
      status: 200,
    });
  });
}

async function mockBootstrapSuccess(page: Page, requests: unknown[] = []) {
  await page.route("**/api/auth/bootstrap_admin", async (route) => {
    requests.push(route.request().postDataJSON());
    await route.fulfill({
      contentType: "application/json",
      headers: {
        "set-cookie": "aq_session=playwright; Path=/; HttpOnly; SameSite=Lax",
      },
      json: {
        user: {
          id: "00000000-0000-4000-8000-000000000001",
          email: "admin@example.com",
          role: "owner",
        },
        first_token: "aq_live_playwright_first_token",
      },
      status: 200,
    });
  });
}

async function attachScreenshot(page: Page, testInfo: TestInfo, name: string) {
  const screenshotPath = path.join(
    process.cwd(),
    "test-results",
    "aq309",
    `${name}.png`,
  );
  await mkdir(path.dirname(screenshotPath), { recursive: true });
  await page.screenshot({ fullPage: true, path: screenshotPath });
  await testInfo.attach(name, {
    path: screenshotPath,
    contentType: "image/png",
  });
}

test("setup-fields-count renders email password confirm only", async ({
  page,
}) => {
  await mockNeedsBootstrap(page);

  await page.goto("/setup");

  await expect(page.locator("form input")).toHaveCount(3);
  await expect(page.getByLabel("Admin email")).toBeVisible();
  await expect(page.getByLabel("Password", { exact: true })).toBeVisible();
  await expect(page.getByLabel("Confirm password")).toBeVisible();
  await expect(page.getByLabel(["AQ_ADMIN", "PASSCODE"].join("_"))).toHaveCount(0);
});

test("setup-first-run-warning renders the security warning", async ({
  page,
}, testInfo) => {
  await mockNeedsBootstrap(page);

  await page.goto("/setup");

  const warning = page.getByRole("note", { name: "First-run security" });
  await expect(warning).toBeVisible();
  await expect(warning).toContainText(
    "Complete setup before exposing this URL publicly.",
  );
  await expect(warning.getByRole("link", { name: "First-run security" })).toHaveAttribute(
    "href",
    /#first-run-security$/,
  );
  await attachScreenshot(page, testInfo, "aq309-setup-warning-1280x800");
});

test("setup-warning-non-blocking submits with warning visible", async ({
  page,
}) => {
  const requests: unknown[] = [];
  await mockNeedsBootstrap(page);
  await mockBootstrapSuccess(page, requests);

  await page.goto("/setup");

  await expect(
    page.getByRole("note", { name: "First-run security" }),
  ).toBeVisible();
  await page.getByLabel("Admin email").fill("admin@example.com");
  await page.getByLabel("Password", { exact: true }).fill("CorrectHorse1!");
  await page.getByLabel("Confirm password").fill("CorrectHorse1!");
  await page.getByRole("button", { name: /create admin account/i }).click();

  await expect(
    page.getByRole("heading", { level: 1, name: "You're in." }),
  ).toBeVisible();
  expect(requests).toEqual([
    {
      email: "admin@example.com",
      password: "CorrectHorse1!",
    },
  ]);
});

test("bootstraps the first admin and reveals the first API token once", async ({
  page,
}) => {
  const requests: unknown[] = [];

  await mockNeedsBootstrap(page);
  await mockBootstrapSuccess(page, requests);

  await page.goto("/setup");

  await expect(
    page.getByRole("heading", { level: 1, name: "Set up this instance" }),
  ).toBeVisible();
  await expect(page.getByLabel("Instance name")).toHaveCount(0);

  await page.getByLabel("Admin email").fill("admin@example.com");
  await page.getByLabel("Password", { exact: true }).fill("CorrectHorse1!");
  await page.getByLabel("Confirm password").fill("CorrectHorse1!");
  await expect(page.getByText("Strong")).toBeVisible();

  await page.getByRole("button", { name: /create admin account/i }).click();

  await expect(
    page.getByRole("heading", { level: 1, name: "You're in." }),
  ).toBeVisible();
  await expect(page.getByText("aq_live_playwright_first_token")).toBeVisible();
  await expect(page.getByText("won't be shown again")).toBeVisible();
  await expect(page.getByRole("button", { name: /^copy$/i })).toBeVisible();
  await expect(
    page.getByRole("button", { name: /continue to dashboard/i }),
  ).toBeVisible();
  expect(requests).toEqual([
    {
      email: "admin@example.com",
      password: "CorrectHorse1!",
    },
  ]);
});

test("redirects setup to login after bootstrap has already run", async ({
  page,
}) => {
  await page.route("**/api/auth/bootstrap_status", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      json: { needs_bootstrap: false },
      status: 200,
    });
  });

  await page.goto("/setup");

  await expect(page).toHaveURL(/\/login$/);
});

test("surfaces completed bootstrap conflicts", async ({
  page,
}) => {
  await mockNeedsBootstrap(page);

  await page.route("**/api/auth/bootstrap_admin", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      json: {
        message: "Bootstrap admin already exists",
      },
      status: 409,
    });
  });

  await page.goto("/setup");
  await page.getByLabel("Admin email").fill("admin@example.com");
  await page.getByLabel("Password", { exact: true }).fill("CorrectHorse1!");
  await page.getByLabel("Confirm password").fill("CorrectHorse1!");
  await page.getByRole("button", { name: /create admin account/i }).click();

  await expect(
    page.getByText("Setup has already been completed. Sign in instead."),
  ).toBeVisible();
});
