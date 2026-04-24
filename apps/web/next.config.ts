import path from "node:path";
import { createRequire } from "node:module";

import type { NextConfig } from "next";

const require = createRequire(import.meta.url);
const rootPackageJson = require("../../package.json") as {
  version?: string;
};

const nextConfig: NextConfig = {
  env: {
    NEXT_PUBLIC_AQ_VERSION: rootPackageJson.version ?? "0.1.0-alpha",
  },
  reactStrictMode: true,
  output: "standalone",
  outputFileTracingRoot: path.join(__dirname, "..", ".."),
};

export default nextConfig;
