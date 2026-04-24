import { readFileSync } from "node:fs";

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { SplitPitch } from "./split-pitch";

describe("SplitPitch", () => {
  it("renders the locked auth pitch copy with caller content in the right pane", () => {
    const markup = renderToStaticMarkup(
      createElement(
        SplitPitch,
        { variant: "setup" },
        createElement("form", null, "Setup form"),
      ),
    );

    expect(markup).toContain(">AQ<");
    expect(markup).toContain(">AgenticQueue<");
    expect(markup).toContain("A read-only queue for the agents in your org.");
    expect(markup).toContain(
      "Watch pipelines, work, decisions and learnings in real time.",
    );
    expect(markup).toContain(
      "Self-hosted. One binary. Bring your own model.",
    );
    expect(markup).toContain(
      "Cookie auth for humans, bearer tokens for agents",
    );
    expect(markup).toContain("SQLite by default");
    expect(markup).toContain("Postgres optional");
    expect(markup).toContain("MCP-compatible");
    expect(markup).toContain("OpenTelemetry out of the box");
    expect(markup).toContain("v0.14.2");
    expect(markup).toContain("commit a7c3f2e");
    expect(markup).toContain(">Setup form<");
  });

  it("keeps auth design tokens, fonts, and the grid scoped to the auth route", () => {
    const layoutSource = readFileSync(
      "apps/web/app/(auth)/layout.tsx",
      "utf8",
    );
    const cssSource = readFileSync(
      "apps/web/app/(auth)/auth-layout.module.css",
      "utf8",
    );

    expect(layoutSource).toContain("from \"next/font/google\"");
    expect(layoutSource).toContain("Inter(");
    expect(layoutSource).toContain("JetBrains_Mono(");
    expect(cssSource).toContain("--bg: #faf8f4");
    expect(cssSource).toContain("--ink: #1a1a1a");
    expect(cssSource).toContain("--accent: #3f76ff");
    expect(cssSource).toContain("--font-sans");
    expect(cssSource).toContain("--font-mono");
    expect(cssSource).toContain("32px 32px");
    expect(cssSource).not.toContain(":root");
    expect(cssSource).not.toContain("body::before");
  });
});
