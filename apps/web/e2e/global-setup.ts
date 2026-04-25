import { spawnSync } from "node:child_process";
import path from "node:path";

const webDir = path.resolve(__dirname, "..");
const repoRoot = path.resolve(webDir, "..", "..");
const directDbPort = process.env.AGENTICQUEUE_DB_PORT ?? process.env.DB_PORT ?? "54329";
const testDatabaseUrl =
  process.env.AGENTICQUEUE_DATABASE_URL_TEST ??
  process.env.DATABASE_URL_TEST ??
  `postgresql+asyncpg://agenticqueue:agenticqueue@127.0.0.1:${directDbPort}/agenticqueue_test`;

function runScript(command: string, args: string[]) {
  return spawnSync(command, args, {
    cwd: repoRoot,
    env: {
      ...process.env,
      AGENTICQUEUE_USE_TEST_DATABASE: "1",
      AGENTICQUEUE_DATABASE_URL_TEST: testDatabaseUrl,
      DATABASE_URL_TEST: testDatabaseUrl,
    },
    stdio: "inherit",
  });
}

function runE2eDbCommand(action: "setup" | "teardown") {
  const python = process.env.AQ_E2E_PYTHON ?? "python";
  let result = runScript(python, ["apps/api/scripts/e2e_test_db.py", action]);

  if (result.status !== 0 && process.env.AQ_E2E_PYTHON === undefined) {
    const uv = process.platform === "win32" ? "uv.exe" : "uv";
    result = runScript(uv, [
      "run",
      "python",
      "apps/api/scripts/e2e_test_db.py",
      action,
    ]);
  }

  if (result.status !== 0) {
    throw new Error(
      `e2e test DB ${action} failed with exit ${result.status ?? result.error?.message}`,
    );
  }
}

export default async function globalSetup() {
  runE2eDbCommand("setup");
}
