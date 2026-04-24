import { expect, test } from "@playwright/test";

test("bootstraps the first admin and reveals the first API token once", async ({
  page,
}) => {
  const requests: unknown[] = [];

  await page.route("**/api/auth/bootstrap_status", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      json: { needs_bootstrap: true },
      status: 200,
    });
  });
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

  await page.goto("/setup");

  await expect(
    page.getByRole("heading", { level: 1, name: "Set up this instance" }),
  ).toBeVisible();
  await expect(page.getByLabel("Instance name")).toHaveCount(0);

  await page.getByLabel("Admin email").fill("admin@example.com");
  await page.getByLabel("AQ_ADMIN_PASSCODE").fill("local-passcode");
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
      passcode: "local-passcode",
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

test("surfaces bootstrap passcode and server configuration errors distinctly", async ({
  page,
}) => {
  await page.route("**/api/auth/bootstrap_status", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      json: { needs_bootstrap: true },
      status: 200,
    });
  });

  let status = 401;
  await page.route("**/api/auth/bootstrap_admin", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      json: {
        message:
          status === 401
            ? "Invalid bootstrap passcode"
            : "AQ_ADMIN_PASSCODE must be set before bootstrap",
      },
      status,
    });
  });

  await page.goto("/setup");
  await page.getByLabel("Admin email").fill("admin@example.com");
  await page.getByLabel("AQ_ADMIN_PASSCODE").fill("wrong-passcode");
  await page.getByLabel("Password", { exact: true }).fill("CorrectHorse1!");
  await page.getByLabel("Confirm password").fill("CorrectHorse1!");
  await page.getByRole("button", { name: /create admin account/i }).click();

  await expect(page.getByText("Bootstrap passcode is incorrect.")).toBeVisible();

  status = 503;
  await page.getByLabel("AQ_ADMIN_PASSCODE").fill("local-passcode");
  await page.getByRole("button", { name: /create admin account/i }).click();

  await expect(
    page.getByText("AQ_ADMIN_PASSCODE is not configured on the server."),
  ).toBeVisible();
});
