import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import {
  LoginScreen,
  PrimaryNav,
  type NavCounts,
} from "./agenticqueue-web-app";

describe("LoginScreen", () => {
  it("renders a masked API token input with hardened text-entry attributes", () => {
    const markup = renderToStaticMarkup(
      createElement(LoginScreen, {
        errorMessage: null,
        onLogin: async () => {},
      }),
    );

    expect(markup).toContain('type="password"');
    expect(markup).toContain('autoComplete="off"');
    expect(markup).toContain('spellCheck="false"');
    expect(markup).toContain('autoCapitalize="off"');
    expect(markup).toContain('inputMode="text"');
    expect(markup.toLowerCase()).toContain(">show<");
  });
});

describe("PrimaryNav", () => {
  it("renders live badge counts and omits badges when counts are missing", () => {
    const counts: NavCounts = {
      pipelines: 3,
      work: 8,
      analytics: 5,
      graph: 4,
      decisions: 2,
    };

    const markup = renderToStaticMarkup(
      createElement(PrimaryNav, {
        counts,
        pathname: "/work",
      }),
    );

    expect(markup).toContain(">Pipelines<");
    expect(markup).toContain(">3<");
    expect(markup).toContain(">Work<");
    expect(markup).toContain(">8<");
    expect(markup).toContain(">Analytics<");
    expect(markup).toContain(">5<");
    expect(markup).toContain(">Graph<");
    expect(markup).toContain(">4<");
    expect(markup).toContain(">Decisions<");
    expect(markup).toContain(">2<");
    expect(markup).toContain(">Learnings<");
    expect(markup).not.toContain(">0<");
  });
});
