import { NextRequest, NextResponse } from "next/server";

import {
  API_BASE_URL,
  authHeadersFromRequest,
  csrfTokenFromRequest,
  unauthorizedSessionResponse,
} from "../_upstream";

type TokenListResponse = {
  actor: {
    id: string;
  };
  tokens: unknown[];
};

export async function GET(request: NextRequest) {
  const headers = authHeadersFromRequest(request);
  if (!headers) {
    return unauthorizedSessionResponse();
  }

  const upstream = await fetch(new URL("/v1/auth/tokens", API_BASE_URL), {
    headers,
    cache: "no-store",
    signal: request.signal,
  });
  const payload = await upstream.json().catch(() => null);

  return NextResponse.json(payload, {
    status: upstream.status || (upstream.ok ? 200 : 500),
  });
}

export async function POST(request: NextRequest) {
  const headers = authHeadersFromRequest(request);
  if (!headers) {
    return unauthorizedSessionResponse();
  }

  const csrfToken = csrfTokenFromRequest(request);
  if (csrfToken) {
    headers.set("X-CSRF-Token", csrfToken);
  }

  const current = await fetch(new URL("/v1/auth/tokens", API_BASE_URL), {
    headers,
    cache: "no-store",
    signal: request.signal,
  });
  const currentPayload = (await current.json().catch(() => null)) as
    | TokenListResponse
    | { message?: string; error?: string }
    | null;
  if (!current.ok || currentPayload === null || !("actor" in currentPayload)) {
    return NextResponse.json(currentPayload, {
      status: current.status || 500,
    });
  }

  headers.set("Content-Type", "application/json");
  const upstream = await fetch(new URL("/v1/auth/tokens", API_BASE_URL), {
    method: "POST",
    headers,
    body: JSON.stringify({
      actor_id: currentPayload.actor.id,
      scopes: ["admin"],
      expires_at: null,
    }),
    cache: "no-store",
    signal: request.signal,
  });
  const payload = await upstream.json().catch(() => null);

  return NextResponse.json(payload, {
    status: upstream.status || (upstream.ok ? 201 : 500),
  });
}
