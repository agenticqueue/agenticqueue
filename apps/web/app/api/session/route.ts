import { NextRequest, NextResponse } from "next/server";

type AuthSessionResponse = {
  actor: {
    id: string;
    handle: string;
    actor_type: string;
    display_name: string;
  };
  tokens: unknown[];
};

const API_BASE_URL =
  process.env.AGENTICQUEUE_API_BASE_URL ??
  process.env.NEXT_PUBLIC_AGENTICQUEUE_API_BASE_URL ??
  "http://127.0.0.1:8010";

export async function POST(request: NextRequest) {
  const body = (await request.json().catch(() => null)) as
    | { token?: string }
    | null;

  const token = body?.token?.trim();

  if (!token) {
    return NextResponse.json(
      { error: "API token is required." },
      { status: 400 },
    );
  }

  const upstream = await fetch(`${API_BASE_URL}/v1/auth/tokens`, {
    headers: {
      Authorization: `Bearer ${token}`,
    },
    cache: "no-store",
  });

  const payload = (await upstream.json().catch(() => null)) as
    | AuthSessionResponse
    | { message?: string }
    | null;

  if (!upstream.ok || payload === null || !("actor" in payload)) {
    const message =
      payload && "message" in payload && typeof payload.message === "string"
        ? payload.message
        : "Token validation failed.";

    return NextResponse.json(
      { error: message, status: upstream.status },
      { status: upstream.status || 500 },
    );
  }

  return NextResponse.json({
    actor: payload.actor,
    tokenCount: payload.tokens.length,
    apiBaseUrl: API_BASE_URL,
  });
}
