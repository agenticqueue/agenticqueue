import { spawnSync } from "node:child_process";
import path from "node:path";

const webDir = path.resolve(__dirname, "..");
const repoRoot = path.resolve(webDir, "..", "..");
const directDbPort = process.env.AGENTICQUEUE_DB_PORT ?? process.env.DB_PORT ?? "54329";
const testDatabaseUrl =
  process.env.AGENTICQUEUE_DATABASE_URL_TEST ??
  process.env.DATABASE_URL_TEST ??
  `postgresql+asyncpg://agenticqueue:agenticqueue@127.0.0.1:${directDbPort}/agenticqueue_test`;

export default async function globalTeardown() {
  const env = {
    ...process.env,
    AGENTICQUEUE_USE_TEST_DATABASE: "1",
    AGENTICQUEUE_DATABASE_URL_TEST: testDatabaseUrl,
    DATABASE_URL_TEST: testDatabaseUrl,
  };
  const python = process.env.AQ_E2E_PYTHON ?? "python";
  let result = spawnSync(
    python,
    ["apps/api/scripts/e2e_test_db.py", "teardown"],
    { cwd: repoRoot, env, stdio: "inherit" },
  );

  if (result.status !== 0 && process.env.AQ_E2E_PYTHON === undefined) {
    const uv = process.platform === "win32" ? "uv.exe" : "uv";
    result = spawnSync(
      uv,
      ["run", "python", "apps/api/scripts/e2e_test_db.py", "teardown"],
      { cwd: repoRoot, env, stdio: "inherit" },
    );
  }

  if (result.status !== 0) {
    throw new Error(
      `e2e test DB teardown failed with exit ${result.status ?? result.error?.message}`,
    );
  }
}
