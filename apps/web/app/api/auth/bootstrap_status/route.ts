import { NextResponse } from "next/server";

import { getApiBaseUrl } from "@/lib/api-base-url";

const API_BASE_URL = getApiBaseUrl();

export async function GET() {
  try {
    const upstream = await fetch(`${API_BASE_URL}/api/auth/bootstrap_status`, {
      cache: "no-store",
    });
    const payload = (await upstream.json().catch(() => null)) as unknown;

    return NextResponse.json(payload, { status: upstream.status });
  } catch (error: unknown) {
    return NextResponse.json(
      {
        message:
          error instanceof Error
            ? error.message
            : "Bootstrap status is unavailable.",
      },
      { status: 503 },
    );
  }
}
