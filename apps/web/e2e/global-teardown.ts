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
  const command = process.platform === "win32" ? "uv.exe" : "uv";
  const result = spawnSync(
    command,
    ["run", "python", "apps/api/scripts/e2e_test_db.py", "teardown"],
    {
      cwd: repoRoot,
      env: {
        ...process.env,
        AGENTICQUEUE_USE_TEST_DATABASE: "1",
        AGENTICQUEUE_DATABASE_URL_TEST: testDatabaseUrl,
        DATABASE_URL_TEST: testDatabaseUrl,
      },
      stdio: "inherit",
    },
  );

  if (result.status !== 0) {
    throw new Error(`e2e test DB teardown failed with exit ${result.status}`);
  }
}
