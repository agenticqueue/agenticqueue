// @vitest-environment jsdom

import type { ReactElement } from "react";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import { PipelinesView } from "./pipelines-view";

(
  globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }
).IS_REACT_ACT_ENVIRONMENT = true;

const roots: Root[] = [];

const inProgressPayload = {
  state: "in_progress",
  count: 1,
  generated_at: "2026-04-21T13:03:20.069Z",
  pipelines: [
    {
      id: "pipeline-1",
      slug: "ingestion-core",
      name: "Realtime ingestion rebuild",
      goal: "Stabilize the ingest chain before the broader execution rollout.",
      state: "in_progress",
      tone: "info",
      progress: { done: 1, total: 3 },
      autonomy: { label: "HITL required · tier 3", tone: "warn" },
      attention: {
        failed: 0,
        needs_review: 0,
        running: 1,
        queued: 1,
        blocked: 0,
      },
      started_at: "2026-04-21T12:45:00.000Z",
      updated_at: "2026-04-21T13:00:00.000Z",
      completed_at: null,
      tasks: [],
    },
  ],
};

const donePayload = {
  state: "done",
  count: 0,
  generated_at: "2026-04-21T13:03:20.069Z",
  pipelines: [],
};

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

function jsonResponse(payload: unknown) {
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: {
      "Content-Type": "application/json",
    },
  });
}

async function flushEffects() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
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
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("PipelinesView", () => {
  it("renders skeleton rows instead of plain loading text while the read model is pending", () => {
    vi.stubGlobal("fetch", vi.fn(() => new Promise(() => {})));

    const container = render(<PipelinesView authToken="aq_live_test" />);

    expect(container.querySelector(".aq-state-loader")).not.toBeNull();
    expect(container.querySelectorAll(".aq-pipeline-skeleton-row")).toHaveLength(
      3,
    );
  });

  it("renders a retry button and refetches both pipeline queries after an error", async () => {
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new Error("upstream failed"))
      .mockRejectedValueOnce(new Error("upstream failed"))
      .mockResolvedValueOnce(jsonResponse(inProgressPayload))
      .mockResolvedValueOnce(jsonResponse(donePayload));

    vi.stubGlobal("fetch", fetchMock);

    const container = render(<PipelinesView authToken="aq_live_test" />);

    await flushEffects();

    const retryButton = container.querySelector<HTMLButtonElement>(
      ".aq-state-retry",
    );

    expect(retryButton).not.toBeNull();

    await act(async () => {
      retryButton?.dispatchEvent(
        new MouseEvent("click", {
          bubbles: true,
        }),
      );
      await Promise.resolve();
    });

    expect(fetchMock).toHaveBeenCalledTimes(4);
  });
});
