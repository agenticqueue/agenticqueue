import { mkdir } from "node:fs/promises";
import path from "node:path";

import { expect, test, type Page, type TestInfo } from "@playwright/test";

import { mockShellReadApis, seedAuthenticatedSession } from "./helpers";

test.use({ viewport: { height: 800, width: 1280 } });

type TokenRow = {
  id: string;
  name: string;
  token_preview: string;
  created_at: string;
  last_used_at: string | null;
};

const bootstrapToken: TokenRow = {
  id: "00000000-0000-4000-8000-000000000001",
  name: "bootstrap",
  token_preview: "aq_live_...",
  created_at: "2026-04-25T14:00:00.000Z",
  last_used_at: "2026-04-25T14:15:00.000Z",
};

async function attachScreenshot(page: Page, testInfo: TestInfo, name: string) {
  const screenshotPath = path.join(
    process.cwd(),
    "test-results",
    "aq312",
    `${name}.png`,
  );
  await mkdir(path.dirname(screenshotPath), { recursive: true });
  await page.screenshot({ fullPage: true, path: screenshotPath });
  await testInfo.attach(name, {
    path: screenshotPath,
    contentType: "image/png",
  });
}

async function openTokensPage(page: Page, tokens: TokenRow[] = [bootstrapToken]) {
  let currentTokens = [...tokens];
  await seedAuthenticatedSession(page);
  await mockShellReadApis(page);
  await page.route("**/api/auth/tokens", async (route) => {
    const request = route.request();
    if (request.method() === "GET") {
      await route.fulfill({
        contentType: "application/json",
        json: { tokens: currentTokens },
        status: 200,
      });
      return;
    }

    if (request.method() === "POST") {
      const payload = request.postDataJSON() as { name?: string };
      const created: TokenRow = {
        id: "00000000-0000-4000-8000-0000000000cd",
        name: payload.name ?? "",
        token_preview: "aq_live_...",
        created_at: "2026-04-25T15:00:00.000Z",
        last_used_at: null,
      };
      currentTokens = [...currentTokens, created];
      await route.fulfill({
        contentType: "application/json",
        json: {
          ...created,
          token: `aq_live_${created.name}_full_token_value_1234567890`,
        },
        status: 200,
      });
      return;
    }

    await route.fulfill({ status: 405 });
  });
  await page.route("**/api/auth/tokens/*", async (route) => {
    if (route.request().method() !== "DELETE") {
      await route.fulfill({ status: 405 });
      return;
    }
    const tokenId = route.request().url().split("/").at(-1);
    currentTokens = currentTokens.filter((token) => token.id !== tokenId);
    await route.fulfill({ status: 204 });
  });

  await page.goto("/settings/tokens");
  await expect(
    page.getByRole("heading", { level: 1, name: "API keys" }),
  ).toBeVisible();
}

test("settings-tokens-happy-path admin creates copies and revokes a token", async ({
  page,
}, testInfo) => {
  await openTokensPage(page);

  await expect(page.getByText("bootstrap")).toBeVisible();
  await expect(page.getByRole("button", { name: "New API key" })).toBeVisible();

  await page.getByRole("button", { name: "New API key" }).click();
  await page.getByLabel("Token name").fill("codex");
  await page.getByRole("button", { name: "Create API key" }).click();

  await expect(
    page.getByText("aq_live_codex_full_token_value_1234567890"),
  ).toBeVisible();
  await page.getByRole("button", { name: "Copy token" }).click();
  await expect(page.getByRole("button", { name: "Copied" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Continue" })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Continue" })).toBeEnabled({
    timeout: 2_000,
  });
  await page.getByRole("button", { name: "Continue" }).click();

  await expect(page.getByText("codex")).toBeVisible();
  await expect(page.getByText("aq_live_codex_full_token_value_1234567890")).toHaveCount(0);
  await attachScreenshot(page, testInfo, "aq312-settings-tokens-two-rows");

  await page.getByRole("button", { name: "Revoke codex" }).click();
  await expect(page.getByText("codex")).toHaveCount(0);
});

test("settings-tokens-reveal-warning blocks accidental close until one second", async ({
  page,
}, testInfo) => {
  await openTokensPage(page);

  await page.getByRole("button", { name: "New API key" }).click();
  await page.getByLabel("Token name").fill("gemini");
  await page.getByRole("button", { name: "Create API key" }).click();

  await expect(page.getByText("will not be shown again")).toBeVisible();
  await expect(page.getByRole("button", { name: "Continue" })).toBeDisabled();
  await attachScreenshot(page, testInfo, "aq312-token-reveal-warning");
  await expect(page.getByRole("button", { name: "Continue" })).toBeEnabled({
    timeout: 2_000,
  });
});
