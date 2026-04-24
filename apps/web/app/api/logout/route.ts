import { NextRequest, NextResponse } from "next/server";

import {
  API_BASE_URL,
  appendUpstreamSetCookie,
  authHeadersFromRequest,
  csrfTokenFromRequest,
} from "../_upstream";

export async function POST(request: NextRequest) {
  const headers = authHeadersFromRequest(request) ?? new Headers();
  const csrfToken = csrfTokenFromRequest(request);
  if (csrfToken) {
    headers.set("X-CSRF-Token", csrfToken);
  }

  const upstream = await fetch(new URL("/v1/auth/logout", API_BASE_URL), {
    method: "POST",
    headers,
    cache: "no-store",
    signal: request.signal,
  });
  const payload = await upstream.json().catch(() => ({ ok: upstream.ok }));
  const response = NextResponse.json(payload, {
    status: upstream.status || (upstream.ok ? 200 : 500),
  });
  appendUpstreamSetCookie(upstream, response);

  return response;
}
