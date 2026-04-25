import { NextRequest, NextResponse } from "next/server";

import { getApiBaseUrl } from "@/lib/api-base-url";

const API_BASE_URL = getApiBaseUrl();

export async function GET(request: NextRequest) {
  const upstream = await fetch(`${API_BASE_URL}/api/auth/tokens`, {
    headers: forwardCookie(request),
    cache: "no-store",
  });
  return proxyJson(upstream);
}

export async function POST(request: NextRequest) {
  const body = (await request.json().catch(() => null)) as unknown;
  const upstream = await fetch(`${API_BASE_URL}/api/auth/tokens`, {
    method: "POST",
    headers: {
      ...forwardCookie(request),
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
    cache: "no-store",
  });
  return proxyJson(upstream);
}

function forwardCookie(request: NextRequest): Record<string, string> {
  const cookie = request.headers.get("cookie");
  return cookie ? { cookie } : {};
}

async function proxyJson(upstream: Response) {
  const payload = (await upstream.json().catch(() => null)) as unknown;
  return NextResponse.json(payload, { status: upstream.status || 500 });
}
