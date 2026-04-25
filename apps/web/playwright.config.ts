import path from "node:path";

import { defineConfig, devices } from "@playwright/test";

const webDir = __dirname;
const repoRoot = path.resolve(webDir, "..", "..");
const authApiPort = process.env.AQ_E2E_AUTH_API_PORT ?? "3127";
const webPort = process.env.AQ_E2E_WEB_PORT ?? "3005";
const directDbPort = process.env.AGENTICQUEUE_DB_PORT ?? process.env.DB_PORT ?? "54329";
const e2eDatabaseUrl =
  process.env.AGENTICQUEUE_DATABASE_URL_TEST ??
  process.env.DATABASE_URL_TEST ??
  `postgresql+asyncpg://agenticqueue:agenticqueue@127.0.0.1:${directDbPort}/agenticqueue_test`;
const e2eDatabaseEnv = {
  AGENTICQUEUE_USE_TEST_DATABASE: "1",
  AGENTICQUEUE_DATABASE_URL_TEST: e2eDatabaseUrl,
  DATABASE_URL_TEST: e2eDatabaseUrl,
};

export default defineConfig({
  testDir: path.join(webDir, "e2e"),
  globalSetup: path.join(webDir, "e2e", "global-setup.ts"),
  globalTeardown: path.join(webDir, "e2e", "global-teardown.ts"),
  timeout: 30_000,
  workers: 1,
  expect: {
    timeout: 5_000,
  },
  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 2 : 0,
  reporter: "list",
  use: {
    baseURL: `http://127.0.0.1:${webPort}`,
    trace: "on-first-retry",
  },
  projects: [
    {
      name: "chromium",
      use: {
        ...devices["Desktop Chrome"],
      },
    },
  ],
  webServer: [
    {
      command: "node e2e/support/auth-api-server.mjs",
      cwd: webDir,
      env: {
        ...process.env,
        ...e2eDatabaseEnv,
        AQ_E2E_AUTH_API_PORT: authApiPort,
      },
      reuseExistingServer: !process.env.CI,
      stdout: "pipe",
      stderr: "pipe",
      url: `http://127.0.0.1:${authApiPort}/v1/health`,
      timeout: 120_000,
    },
    {
      command:
        `npm --workspace @agenticqueue/web run dev -- --hostname 127.0.0.1 --port ${webPort}`,
      cwd: repoRoot,
      env: {
        ...process.env,
        ...e2eDatabaseEnv,
        AQ_API_BASE_URL: `http://127.0.0.1:${authApiPort}`,
        NEXT_TELEMETRY_DISABLED: "1",
      },
      reuseExistingServer: !process.env.CI,
      stdout: "pipe",
      stderr: "pipe",
      url: `http://127.0.0.1:${webPort}/login`,
      timeout: 120_000,
    },
  ],
});
