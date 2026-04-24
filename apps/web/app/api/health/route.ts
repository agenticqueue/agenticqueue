import { NextResponse } from "next/server";

import { getApiBaseUrl } from "@/lib/api-base-url";
import { AQ_BUILD_VERSION } from "@/lib/build-version";

const API_BASE_URL = getApiBaseUrl();

export async function GET() {
  try {
    const upstream = await fetch(`${API_BASE_URL}/v1/health`, {
      cache: "no-store",
    });
    const payload = (await upstream.json().catch(() => null)) as
      | { status?: string; version?: string }
      | null;

    if (!upstream.ok) {
      return NextResponse.json(
        {
          status: "degraded",
          version: AQ_BUILD_VERSION,
          deps: {
            api: {
              status: "error",
              http_status: upstream.status,
              url: API_BASE_URL,
              version:
                payload && typeof payload.version === "string"
                  ? payload.version
                  : null,
            },
          },
        },
        { status: 503 },
      );
    }

    const apiStatus =
      payload && typeof payload.status === "string" ? payload.status : "ok";

    return NextResponse.json({
      status: apiStatus,
      version: AQ_BUILD_VERSION,
      deps: {
        api: {
          status: apiStatus,
          url: API_BASE_URL,
          version:
            payload && typeof payload.version === "string"
              ? payload.version
              : null,
        },
      },
    });
  } catch (error: unknown) {
    return NextResponse.json(
      {
        status: "degraded",
        version: AQ_BUILD_VERSION,
        deps: {
          api: {
            status: "unreachable",
            url: API_BASE_URL,
            error:
              error instanceof Error ? error.message : "Unknown upstream failure.",
          },
        },
      },
      { status: 503 },
    );
  }
}
