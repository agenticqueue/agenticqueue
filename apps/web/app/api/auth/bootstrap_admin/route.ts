import { NextRequest, NextResponse } from "next/server";

import { getApiBaseUrl } from "@/lib/api-base-url";

const API_BASE_URL = getApiBaseUrl();

export async function POST(request: NextRequest) {
  const body = await request.text();

  try {
    const upstream = await fetch(`${API_BASE_URL}/api/auth/bootstrap_admin`, {
      method: "POST",
      headers: {
        "Content-Type": request.headers.get("content-type") ?? "application/json",
      },
      body,
      cache: "no-store",
    });
    const payload = (await upstream.json().catch(() => null)) as unknown;
    const response = NextResponse.json(payload, { status: upstream.status });
    const setCookie = upstream.headers.get("set-cookie");

    if (setCookie) {
      response.headers.set("set-cookie", setCookie);
    }
    return response;
  } catch (error: unknown) {
    return NextResponse.json(
      {
        message:
          error instanceof Error ? error.message : "Bootstrap admin failed.",
      },
      { status: 503 },
    );
  }
}
