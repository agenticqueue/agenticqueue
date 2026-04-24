import { describe, expect, it } from "vitest";

import {
  DEFAULT_API_BASE_URL,
  getApiBaseUrl,
} from "./api-base-url";

describe("getApiBaseUrl", () => {
  it("prefers AQ_API_BASE_URL over deprecated aliases", () => {
    expect(
      getApiBaseUrl({
        AQ_API_BASE_URL: "http://canonical:8000",
        AQ_API_URL: "http://legacy-short:8000",
        AGENTICQUEUE_API_BASE_URL: "http://legacy-long:8000",
        NEXT_PUBLIC_AGENTICQUEUE_API_BASE_URL: "http://public:8000",
      }),
    ).toBe("http://canonical:8000");
  });

  it("falls back through deprecated aliases before the default", () => {
    expect(
      getApiBaseUrl({
        AQ_API_URL: "http://legacy-short:8000",
        AGENTICQUEUE_API_BASE_URL: "http://legacy-long:8000",
        NEXT_PUBLIC_AGENTICQUEUE_API_BASE_URL: "http://public:8000",
      }),
    ).toBe("http://legacy-short:8000");

    expect(
      getApiBaseUrl({
        AGENTICQUEUE_API_BASE_URL: "http://legacy-long:8000",
        NEXT_PUBLIC_AGENTICQUEUE_API_BASE_URL: "http://public:8000",
      }),
    ).toBe("http://legacy-long:8000");

    expect(
      getApiBaseUrl({
        NEXT_PUBLIC_AGENTICQUEUE_API_BASE_URL: "http://public:8000",
      }),
    ).toBe("http://public:8000");
  });

  it("returns the default URL when no env var is configured", () => {
    expect(getApiBaseUrl({})).toBe(DEFAULT_API_BASE_URL);
  });
});
