import path from "node:path";

import { defineConfig, devices } from "@playwright/test";

const webDir = __dirname;
const repoRoot = path.resolve(webDir, "..", "..");

export default defineConfig({
  testDir: path.join(webDir, "e2e"),
  timeout: 30_000,
  expect: {
    timeout: 5_000,
  },
  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 2 : 0,
  reporter: "list",
  use: {
    baseURL: "http://127.0.0.1:3005",
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
  webServer: {
    command:
      "npm --workspace @agenticqueue/web run dev -- --hostname 127.0.0.1 --port 3005",
    cwd: repoRoot,
    env: {
      ...process.env,
      NEXT_TELEMETRY_DISABLED: "1",
    },
    reuseExistingServer: !process.env.CI,
    stdout: "pipe",
    stderr: "pipe",
    url: "http://127.0.0.1:3005/pipelines",
    timeout: 120_000,
  },
});
