import { NextRequest, NextResponse } from "next/server";

import { getApiBaseUrl } from "@/lib/api-base-url";

const API_BASE_URL = getApiBaseUrl();

export async function GET(request: NextRequest) {
  const authorization = request.headers.get("authorization")?.trim();
  if (!authorization) {
    return NextResponse.json(
      { error: "Authorization header is required." },
      { status: 401 },
    );
  }

  const windowKey = request.nextUrl.searchParams.get("window")?.trim() || "90d";
  const upstreamUrl = new URL("/v1/analytics/metrics", API_BASE_URL);
  upstreamUrl.searchParams.set("window", windowKey);

  const upstream = await fetch(upstreamUrl, {
    headers: {
      Authorization: authorization,
    },
    cache: "no-store",
    signal: request.signal,
  });

  const payload = await upstream.json().catch(() => null);

  if (!upstream.ok || payload === null) {
    const errorMessage =
      payload &&
      typeof payload === "object" &&
      "message" in payload &&
      typeof payload.message === "string"
        ? payload.message
        : payload &&
            typeof payload === "object" &&
            "error" in payload &&
            typeof payload.error === "string"
          ? payload.error
          : "Analytics request failed.";

    return NextResponse.json(
      { error: errorMessage, status: upstream.status },
      { status: upstream.status || 500 },
    );
  }

  return NextResponse.json(payload);
}
