import { dirname } from "node:path";
import { fileURLToPath } from "node:url";

import { FlatCompat } from "@eslint/eslintrc";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const compat = new FlatCompat({ baseDirectory: __dirname });

const config = [
  {
    ignores: [
      "**/node_modules/**",
      "**/.next/**",
      "dist/**",
      "coverage/**",
      "apps/web/next-env.d.ts",
    ],
  },
  {
    settings: {
      next: {
        rootDir: "apps/web/",
      },
    },
    rules: {
      "@next/next/no-html-link-for-pages": "off",
    },
  },
  ...compat.extends("next/core-web-vitals", "next/typescript"),
];

export default config;
