import { mkdir } from "node:fs/promises";
import path from "node:path";

import { expect, test, type Page, type TestInfo } from "@playwright/test";

test.use({ viewport: { height: 800, width: 1280 } });

const E2E_WEB_BASE_URL = `http://127.0.0.1:${process.env.AQ_E2E_WEB_PORT ?? "3005"}`;

async function attachScreenshot(page: Page, testInfo: TestInfo, name: string) {
  const screenshotPath = path.join(
    process.cwd(),
    "test-results",
    "aq209",
    `${name}.png`,
  );
  await mkdir(path.dirname(screenshotPath), { recursive: true });
  await page.screenshot({ fullPage: true, path: screenshotPath });
  await testInfo.attach(name, {
    path: screenshotPath,
    contentType: "image/png",
  });
}

async function openLogin(page: Page) {
  await page.goto("/login");
  await expect(
    page.getByRole("heading", { level: 1, name: "Sign in" }),
  ).toBeVisible();
}

test("renders the split login screen with prototype class parity", async ({
  page,
}, testInfo) => {
  await openLogin(page);

  await expect(page.locator(".split")).toBeVisible();
  await expect(page.locator(".split-left")).toBeVisible();
  await expect(page.locator(".split-right")).toBeVisible();
  await expect(page.locator(".brand-mark")).toContainText("AQ");
  await expect(page.locator(".pitch-feats li")).toHaveCount(3);
  await expect(page.locator(".status-strip")).toContainText(
    "Ready · admin exists · /login",
  );
  await expect(page.locator(".heading")).toContainText("Sign in");
  await expect(page.locator(".field")).toHaveCount(2);
  await expect(page.locator(".input-wrap")).toHaveCount(2);
  await expect(page.locator(".reveal")).toBeVisible();
  await expect(page.locator(".remember")).toBeVisible();
  await expect(page.locator(".primary")).toContainText("Sign in");
  await expect(page.locator(".divider")).toBeVisible();
  await expect(page.locator(".foot")).toBeVisible();
  await expect(page.getByLabel(/username/i)).toHaveCount(0);
  await expect(page.getByLabel("Email")).toBeVisible();
  await expect(page.getByLabel("Password")).toBeVisible();

  await attachScreenshot(page, testInfo, "aq209-login-empty");
});

test("toggles password reveal without changing the form state", async ({
  page,
}) => {
  await openLogin(page);

  const password = page.getByLabel("Password");
  await password.fill("CorrectHorse1!");
  await expect(password).toHaveAttribute("type", "password");

  await page.getByRole("button", { name: /^show$/i }).click();
  await expect(password).toHaveAttribute("type", "text");
  await expect(password).toHaveValue("CorrectHorse1!");

  await page.getByRole("button", { name: /^hide$/i }).click();
  await expect(password).toHaveAttribute("type", "password");
  await expect(password).toHaveValue("CorrectHorse1!");
});

test("shows inline validation for an invalid email before calling the session API", async ({
  page,
}) => {
  let calledSession = false;
  await page.route("**/api/session", async (route) => {
    calledSession = true;
    await route.fulfill({ status: 500 });
  });
  await openLogin(page);

  await page.getByLabel("Email").fill("not-an-email");
  await page.getByLabel("Password").fill("CorrectHorse1!");
  await page.getByRole("button", { name: /^sign in/i }).click();

  await expect(page.getByText("Enter a valid email address.")).toBeVisible();
  expect(calledSession).toBe(false);
});

test("surfaces a 401 session response inline", async ({ page }, testInfo) => {
  await page.route("**/api/session", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      json: { error: "Invalid email or password", status: 401 },
      status: 401,
    });
  });
  await openLogin(page);

  await page.getByLabel("Email").fill("admin@example.com");
  await page.getByLabel("Password").fill("wrong-password");
  await page.getByRole("button", { name: /^sign in/i }).click();

  await expect(page.getByText("Email or password is incorrect.")).toBeVisible();
  await attachScreenshot(page, testInfo, "aq209-login-error");
});

test("disables the form while signing in and redirects to pipelines on success", async ({
  page,
}, testInfo) => {
  let releaseSession: (() => void) | undefined;
  const sessionPaused = new Promise<void>((resolve) => {
    releaseSession = resolve;
  });

  await page.route("**/api/session", async (route) => {
    await sessionPaused;
      await route.fulfill({
        contentType: "application/json",
        headers: {
          "set-cookie": "aq_session=playwright; Path=/; SameSite=Lax",
        },
        json: {
          apiBaseUrl: "http://127.0.0.1:8010",
          user: { email: "admin@example.com", is_admin: true },
      },
      status: 200,
    });
  });
  await openLogin(page);

  await page.getByLabel("Email").fill("admin@example.com");
  await page.getByLabel("Password").fill("CorrectHorse1!");
  await page.getByRole("button", { name: /^sign in/i }).click();

  const submit = page.getByRole("button", { name: /signing in/i });
  await expect(submit).toBeDisabled();
  await expect(page.getByLabel("Email")).toBeDisabled();
  await expect(page.getByLabel("Password")).toBeDisabled();
  await attachScreenshot(page, testInfo, "aq209-login-mid-submit");

  releaseSession?.();
  await expect(page).toHaveURL(/\/pipelines$/);
});

test("persists and clears the remembered email preference", async ({ page }) => {
  await page.route("**/api/session", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      headers: {
        "set-cookie": "aq_session=playwright; Path=/; SameSite=Lax",
      },
      json: {
        apiBaseUrl: "http://127.0.0.1:8010",
        user: { email: "admin@example.com", is_admin: true },
      },
      status: 200,
    });
  });

  await openLogin(page);
  await page.getByLabel("Email").fill("admin@example.com");
  await page.getByLabel("Password").fill("CorrectHorse1!");
  await expect(page.getByLabel("Remember me on this device")).toBeChecked();
  await page.getByRole("button", { name: /^sign in/i }).click();
  await expect(page).toHaveURL(/\/pipelines$/);
  await expect
    .poll(() => page.evaluate(() => localStorage.getItem("aq_email")))
    .toBe("admin@example.com");

  await page.goto("/login");
  await expect(page.getByLabel("Email")).toHaveValue("admin@example.com");
  await page.getByLabel("Email").fill("operator@example.com");
  await page.getByLabel("Password").fill("CorrectHorse1!");
  await page.getByLabel("Remember me on this device").uncheck();
  await page.getByRole("button", { name: /^sign in/i }).click();
  await expect(page).toHaveURL(/\/pipelines$/);
  await expect
    .poll(() => page.evaluate(() => localStorage.getItem("aq_email")))
    .toBeNull();
});

test("keeps the auth grid scoped to login and out of pipelines", async ({
  page,
}) => {
  await openLogin(page);

  const loginGridSize = await page
    .locator('[data-auth-route="login"]')
    .evaluate((node) => getComputedStyle(node, "::before").backgroundSize);
  expect(loginGridSize).toContain("32px 32px");

  await page.context().addCookies([
    {
      name: "aq_session",
      value: "playwright",
      url: E2E_WEB_BASE_URL,
    },
  ]);
  await page.goto("/pipelines");
  await expect(page.locator('[data-auth-route="login"]')).toHaveCount(0);
  const bodyGridSize = await page
    .locator("body")
    .evaluate((node) => getComputedStyle(node, "::before").backgroundSize);
  expect(bodyGridSize).not.toContain("32px 32px");
});
