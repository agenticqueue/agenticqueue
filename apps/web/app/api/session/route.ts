import { NextRequest, NextResponse } from "next/server";

import { getApiBaseUrl } from "@/lib/api-base-url";

type AuthSessionResponse = {
  user: {
    email: string;
    is_admin: boolean;
  };
};

const API_BASE_URL = getApiBaseUrl();

export async function POST(request: NextRequest) {
  const body = (await request.json().catch(() => null)) as unknown;

  if (body === null || typeof body !== "object" || Array.isArray(body)) {
    return NextResponse.json(
      { error: "Session payload is required." },
      { status: 400 },
    );
  }

  const upstream = await fetch(`${API_BASE_URL}/api/session`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
    cache: "no-store",
  });

  const payload = (await upstream.json().catch(() => null)) as
    | AuthSessionResponse
    | { message?: string }
    | null;

  if (!upstream.ok || payload === null || !("user" in payload)) {
    const message =
      payload && "message" in payload && typeof payload.message === "string"
        ? payload.message
        : "Session creation failed.";

    return NextResponse.json(
      { error: message, status: upstream.status },
      { status: upstream.status || 500 },
    );
  }

  const response = NextResponse.json({
    user: payload.user,
    apiBaseUrl: API_BASE_URL,
  });
  const setCookie = upstream.headers.get("set-cookie");
  if (setCookie) {
    response.headers.set("set-cookie", setCookie);
  }
  return response;
}
