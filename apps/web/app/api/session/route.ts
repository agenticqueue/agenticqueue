import { NextRequest, NextResponse } from "next/server";

import {
  API_BASE_URL,
  appendUpstreamSetCookie,
  authHeadersFromRequest,
  unauthorizedSessionResponse,
} from "../_upstream";

type ActorPayload = {
  id: string;
  handle: string;
  actor_type: string;
  display_name: string;
};

type LocalLoginResponse = {
  user: {
    id: string;
    username: string;
    is_admin: boolean;
  };
};

type TokenListResponse = {
  actor: ActorPayload;
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
  const payload = (await upstream.json().catch(() => null)) as
    | TokenListResponse
    | { message?: string; error?: string }
    | null;

  if (!upstream.ok || payload === null || !("actor" in payload)) {
    return NextResponse.json(
      {
        error: extractError(payload, "Local user session required."),
        status: upstream.status,
      },
      { status: upstream.status || 500 },
    );
  }

  return NextResponse.json({
    actor: payload.actor,
    tokenCount: payload.tokens.length,
    apiBaseUrl: API_BASE_URL,
  });
}

export async function POST(request: NextRequest) {
  const body = (await request.json().catch(() => null)) as
    | { username?: string; passcode?: string }
    | null;
  const username = body?.username?.trim();
  const passcode = body?.passcode ?? "";

  if (!username || !passcode) {
    return NextResponse.json(
      { error: "Username and passcode are required." },
      { status: 400 },
    );
  }

  const upstream = await fetch(new URL("/v1/auth/login", API_BASE_URL), {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ username, passcode }),
    cache: "no-store",
    signal: request.signal,
  });
  const payload = (await upstream.json().catch(() => null)) as
    | LocalLoginResponse
    | { message?: string; error?: string }
    | null;

  if (!upstream.ok || payload === null || !("user" in payload)) {
    return NextResponse.json(
      {
        error: extractError(payload, "Invalid username or passcode"),
        status: upstream.status,
      },
      { status: upstream.status || 500 },
    );
  }

  const response = NextResponse.json({
    actor: {
      id: payload.user.id,
      handle: payload.user.username,
      actor_type: payload.user.is_admin ? "admin" : "user",
      display_name: payload.user.username,
    },
    tokenCount: 0,
    apiBaseUrl: API_BASE_URL,
  });
  appendUpstreamSetCookie(upstream, response);

  return response;
}

function extractError(payload: unknown, fallback: string) {
  if (payload && typeof payload === "object") {
    const message =
      "message" in payload && typeof payload.message === "string"
        ? payload.message
        : null;
    const error =
      "error" in payload && typeof payload.error === "string"
        ? payload.error
        : null;

    return message ?? error ?? fallback;
  }

  return fallback;
}
