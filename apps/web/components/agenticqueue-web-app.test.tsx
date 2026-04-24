import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { LoginScreen } from "./agenticqueue-web-app";

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
