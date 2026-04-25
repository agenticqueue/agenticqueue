// @vitest-environment jsdom

import type { ReactElement } from "react";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import LoginPage from "./login/page";
import SetupPage from "./(auth)/setup/page";

const mockedRouter = {
  replace: vi.fn(),
};

vi.mock("next/navigation", () => ({
  useRouter: () => mockedRouter,
}));

(
  globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }
).IS_REACT_ACT_ENVIRONMENT = true;

const roots: Root[] = [];

function render(element: ReactElement) {
  const container = document.createElement("div");
  document.body.appendChild(container);

  const root = createRoot(container);
  roots.push(root);

  act(() => {
    root.render(element);
  });

  return container;
}

afterEach(() => {
  while (roots.length > 0) {
    const root = roots.pop();
    if (root) {
      act(() => {
        root.unmount();
      });
    }
  }

  document.body.innerHTML = "";
  mockedRouter.replace.mockReset();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("auth loading states", () => {
  it("shows a visible loading treatment while login bootstrap status is still checking", () => {
    vi.stubGlobal("fetch", vi.fn(() => new Promise(() => {})));

    const container = render(<LoginPage />);

    expect(container.querySelector(".status-strip")).not.toBeNull();
    expect(container.querySelector(".status-activity")).not.toBeNull();
    expect(container.textContent).toContain("/login");
  });

  it("adds the animated status treatment to the setup loading panel", () => {
    vi.stubGlobal("fetch", vi.fn(() => new Promise(() => {})));

    const container = render(<SetupPage />);

    expect(container.querySelector(".status-strip")).not.toBeNull();
    expect(container.querySelector(".status-activity")).not.toBeNull();
    expect(container.textContent).toContain("/setup");
  });
});
