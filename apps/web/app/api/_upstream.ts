import { NextRequest, NextResponse } from "next/server";

export const API_BASE_URL =
  process.env.AGENTICQUEUE_API_BASE_URL ??
  process.env.NEXT_PUBLIC_AGENTICQUEUE_API_BASE_URL ??
  "http://127.0.0.1:8010";

const CSRF_COOKIE_NAME = "csrf-token";

type SetCookieHeaders = Headers & {
  getSetCookie?: () => string[];
};

export function authHeadersFromRequest(request: NextRequest) {
  const headers = new Headers();
  const authorization = request.headers.get("authorization")?.trim();
  const cookie = request.headers.get("cookie")?.trim();
  let hasAuthHeader = false;

  if (authorization) {
    headers.set("Authorization", authorization);
    hasAuthHeader = true;
  }
  if (cookie) {
    headers.set("Cookie", cookie);
    hasAuthHeader = true;
  }

  return hasAuthHeader ? headers : null;
}

export function unauthorizedSessionResponse() {
  return NextResponse.json(
    { error: "Local user session required." },
    { status: 401 },
  );
}

export function csrfTokenFromRequest(request: NextRequest) {
  return request.cookies.get(CSRF_COOKIE_NAME)?.value ?? null;
}

export function appendUpstreamSetCookie(
  upstream: Response,
  response: NextResponse,
) {
  const headers = upstream.headers as SetCookieHeaders;
  const setCookies =
    typeof headers.getSetCookie === "function"
      ? headers.getSetCookie()
      : splitSetCookieHeader(headers.get("set-cookie"));

  for (const cookie of setCookies) {
    response.headers.append("Set-Cookie", cookie);
  }
}

function splitSetCookieHeader(header: string | null) {
  if (!header) {
    return [];
  }

  return header.split(/,(?=\s*[^;=]+=)/).map((cookie) => cookie.trim());
}
