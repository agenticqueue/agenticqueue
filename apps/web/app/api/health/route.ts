import { NextResponse } from "next/server";

import { getApiBaseUrl } from "@/lib/api-base-url";

const API_BASE_URL = getApiBaseUrl();

const WEB_VERSION = "0.1.0";

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
          version: WEB_VERSION,
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

    return NextResponse.json({
      status: "ok",
      version: WEB_VERSION,
      deps: {
        api: {
          status:
            payload && typeof payload.status === "string"
              ? payload.status
              : "ok",
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
        version: WEB_VERSION,
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
