import { NextRequest, NextResponse } from "next/server";

import { getApiBaseUrl } from "@/lib/api-base-url";

const API_BASE_URL = getApiBaseUrl();

export async function DELETE(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  const upstream = await fetch(`${API_BASE_URL}/api/auth/tokens/${id}`, {
    method: "DELETE",
    headers: forwardCookie(request),
    cache: "no-store",
  });

  if (upstream.status === 204) {
    return new NextResponse(null, { status: 204 });
  }

  const payload = (await upstream.json().catch(() => null)) as unknown;
  return NextResponse.json(payload, { status: upstream.status || 500 });
}

function forwardCookie(request: NextRequest): Record<string, string> {
  const cookie = request.headers.get("cookie");
  return cookie ? { cookie } : {};
}
