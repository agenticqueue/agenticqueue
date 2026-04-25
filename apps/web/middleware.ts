import { NextRequest, NextResponse } from "next/server";

import { getApiBaseUrl } from "@/lib/api-base-url";

const SESSION_COOKIE_NAME = "aq_session";
const API_BASE_URL = getApiBaseUrl();

type BootstrapStatusResponse = {
  needs_bootstrap?: boolean;
};

export async function middleware(request: NextRequest) {
  const needsBootstrap = await getNeedsBootstrap();
  const pathname = request.nextUrl.pathname;

  if (needsBootstrap) {
    return redirect(request, "/setup");
  }

  if (!request.cookies.has(SESSION_COOKIE_NAME)) {
    if (pathname === "/") {
      return NextResponse.rewrite(new URL("/login", request.url));
    }
    const next = `${pathname}${request.nextUrl.search}`;
    return redirect(request, `/login?next=${encodeURIComponent(next || "/")}`);
  }

  return NextResponse.next();
}

async function getNeedsBootstrap() {
  try {
    const response = await fetch(`${API_BASE_URL}/api/auth/bootstrap_status`, {
      cache: "no-store",
    });
    const payload = (await response.json().catch(() => null)) as
      | BootstrapStatusResponse
      | null;

    return response.ok && payload?.needs_bootstrap === true;
  } catch {
    return false;
  }
}

function redirect(request: NextRequest, location: string) {
  return NextResponse.redirect(new URL(location, request.url), 307);
}

export const config = {
  matcher: [
    "/((?!api/|_next/|favicon.ico|setup$|login$|.*\\.(?:svg|png|jpg|jpeg|gif|webp|ico|css|js|map)$).*)",
  ],
};
